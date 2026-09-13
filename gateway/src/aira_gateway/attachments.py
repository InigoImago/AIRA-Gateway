"""Accepting a document or an image (FRD-110).

The gateway **does not parse** what it forwards: this process holds cloud credentials and every
in-flight request, and document parsers are among the most reliably exploitable code there is.
What happens here is decoding, counting and a handful of byte comparisons, in this order:

1. **Is it valid base64?** Invalid is a 400, never a truncated forward.
2. **Is the media type one we accept?** An allow-list, and the deployment can narrow it.
3. **Does the content match what it claims?** A magic-byte sniff that catches a mislabelled upload
   — not a content scanner. Malware scanning is out of scope and a stated risk (`FRD-110` §8).
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

#: The media types AIRA accepts: the **outer bound** across every provider, not a claim about any
#: model. A type inside it must still be declared per model (`FRD-114` FR-7), and the two are
#: intersected against the model about to be dispatched to.
DEFAULT_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/x-javascript",
        "text/javascript",
        "text/plain",
        "text/html",
        "text/md",
        "text/csv",
        "text/xml",
        "text/rtf",
        "image/png",
        "image/jpg",
        "image/jpeg",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)

#: Leading bytes a type must start with, where the format has a recognisable one. Text formats are
#: absent on purpose: "does this look like Markdown" has no answer.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpg": (b"\xff\xd8\xff",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/heic": (b"\x00\x00\x00",),
    "image/heif": (b"\x00\x00\x00",),
}

MAX_PART_BYTES = 6 * 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MAX_PARTS = 16

#: The key naming a part's media type, in either spelling. **A dict carrying one of these together
#: with `data` is inline binary, wherever it sits** — the KIRA shape has no wrapper, so recognising
#: the shape covers every surface where a list of wrapper keys covered one.
_MIME_KEYS = ("mimeType", "mime_type")

#: Wrapper keys of the Gemini shape, where the datum is the *value* under the key; kept so the
#: stored row preserves the wrapper.
_INLINE_KEYS = ("inlineData", "inline_data")


class AttachmentRejected(Exception):
    """An attachment cannot be accepted. Carries a message naming the part and the reason —
    a caller who cannot tell *which* of five parts was refused has to bisect their own request."""


@dataclass(frozen=True, slots=True)
class Limits:
    media_types: frozenset[str] = DEFAULT_MEDIA_TYPES
    max_part_bytes: int = MAX_PART_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES
    max_parts: int = MAX_PARTS


def decode(raw: str, *, index: int) -> bytes:
    """Base64 → bytes, or a refusal naming the part."""
    try:
        # `validate=True`: without it, characters outside the alphabet are silently discarded and
        # a corrupted upload becomes a shorter, valid-looking document.
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentRejected(f"Part {index}: data is not valid base64.") from exc


def check_media_type(media_type: str, limits: Limits, *, index: int) -> None:
    if media_type not in limits.media_types:
        raise AttachmentRejected(
            f"Part {index}: '{media_type}' is not an accepted media type. "
            f"Accepted: {sorted(limits.media_types)}."
        )


def check_signature(media_type: str, data: bytes, *, index: int) -> None:
    """Catch a mislabelled upload. Not a scanner — see the module docstring."""
    signatures = _SIGNATURES.get(media_type)
    if signatures is None:
        return
    if not any(data.startswith(signature) for signature in signatures):
        raise AttachmentRejected(f"Part {index}: the content does not look like '{media_type}'.")


def check_bounds(sizes: list[int], limits: Limits) -> None:
    """Refuse a request that is too large, naming which bound it broke.

    `AIRA_MAX_REQUEST_BYTES` is the outer ceiling; base64 inflates by a third, so an 8 MiB body
    carries at most ~6 MiB of document. These sit inside it so a refusal says *which* limit it hit.
    """
    if len(sizes) > limits.max_parts:
        raise AttachmentRejected(
            f"A request may carry at most {limits.max_parts} attachments ({len(sizes)} given)."
        )
    for index, size in enumerate(sizes):
        if size > limits.max_part_bytes:
            raise AttachmentRejected(
                f"Part {index}: {size} bytes exceeds the {limits.max_part_bytes} allowed per part."
            )
    total = sum(sizes)
    if total > limits.max_total_bytes:
        raise AttachmentRejected(
            f"Attachments total {total} bytes, above the {limits.max_total_bytes} allowed."
        )


def describe(media_type: str, data: bytes, index: int) -> dict[str, object]:
    """What the audit trail keeps about an attachment — never the bytes (`FRD-110` §5.4).

    Stored bytes would make each row megabytes and put uninspected binary inside the retention and
    redaction boundary. The digest links repeated submissions of a file without storing it.
    """
    return {
        "kind": "data",
        "media_type": media_type,
        "bytes": len(data),
        "sha256": sha256(data).hexdigest(),
        "index": index,
    }


def _inline_datum(payload: dict[str, Any]) -> bool:
    """Whether this dict *is* inline binary: a media type and the bytes, together."""
    return any(key in payload for key in _MIME_KEYS) and "data" in payload


def _measure(raw: Any) -> tuple[int, str]:
    """The decoded size and digest of a base64 datum, or `(0, "")` if it is not decodable.

    Never raises: a *response* payload has not been validated, and an upstream returning something
    that is not base64 must not cost the audit row.
    """
    try:
        decoded = base64.b64decode(str(raw), validate=True)
    except ValueError:
        return 0, ""
    return len(decoded), sha256(decoded).hexdigest()


def _summary(raw: Any) -> dict[str, Any]:
    """What is stored in place of the bytes: what they were, not what they said."""
    size, digest = _measure(raw)
    return {"kind": "data", "bytes": size, "sha256": digest}


def strip_attachments(payload: Any) -> Any:
    """Replace inline binary content with its description, wherever it appears.

    Applied to every stored payload **before** redaction and unconditionally — not as a redactor,
    because a deployment that swaps the redactor must not be able to turn this off.
    """
    if isinstance(payload, list):
        return [strip_attachments(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    # The part *is* the datum (the KIRA shape): replaced in place, as there is no wrapper.
    if _inline_datum(payload):
        return {
            **{k: v for k, v in payload.items() if k != "data"},
            "data": _summary(payload.get("data")),
        }

    for key in _INLINE_KEYS:
        inline = payload.get(key)
        if isinstance(inline, dict):
            media_type = str(inline.get("mimeType") or inline.get("mime_type") or "unknown")
            raw = inline.get("data")
            # The *decoded* size, because that is what an audit compares against a byte limit.
            size, digest = _measure(raw)
            return {
                **{k: strip_attachments(v) for k, v in payload.items() if k != key},
                key: {"kind": "data", "media_type": media_type, "bytes": size, "sha256": digest},
            }
    return {key: strip_attachments(value) for key, value in payload.items()}
