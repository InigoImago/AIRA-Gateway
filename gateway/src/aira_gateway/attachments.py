"""Accepting a document or an image (FRD-110).

The gateway **does not parse** what it forwards. That is deliberate and it is what keeps the
attack surface small: this process holds cloud credentials, database connections and every
in-flight request, and PDF and office parsers are among the most reliably exploitable code in
existence. What happens here is decoding, counting and a handful of byte comparisons.

Three checks, in the order they matter:

1. **Is it valid base64?** Invalid is a 400, never a truncated forward.
2. **Is the media type one we accept?** An allow-list, and the deployment can narrow it.
3. **Does the content match what it claims?** A magic-byte sniff — enough to catch a mislabelled
   upload and a trivially disguised payload, and *nothing more than that*. It is not a content
   scanner and this module does not pretend to be one.

Malware scanning is out of scope and its absence is a stated risk (`FRD-110` §8). A deployment
that needs it needs a scanner in front of the gateway or a hook right here.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

#: The media types AIRA accepts, taken from the compatibility contract. What a *model*
#: accepts is narrower and is declared per model (`FRD-114`); the two are intersected, and the
#: intersection is checked against the model about to be dispatched to.
#: **The outer bound, not a claim about any model.** A type outside this set is refused before any
#: model is consulted; a type inside it still has to be declared per model (`FRD-114` FR-7), and
#: the declaration is meant to be evidence.
#:
#: Measured on 2026-08-19, one real file per type against `gemini-2.5-flash` on Vertex
#: (europe-west1). Each file carried a nonsense marker — in the images drawn as **pixels**, so a
#: correct answer is proof the picture was looked at rather than that a header was parsed — and the
#: model was asked for the word alone:
#:
#:     pdf · javascript · plain · html · md · csv · xml · rtf      read, marker returned exactly
#:     png · jpg · jpeg · webp · heic · heif                       read, marker returned exactly
#:     application/x-javascript                                    **refused by Vertex, 400**
#:
#: Vertex's own words for the last one: *"it has a mimeType parameter with value
#: application/x-javascript, which is not supported"*. It stays in this set because the set is the
#: outer bound across every provider and another one may take it; what changed is the *model's*
#: declaration, which now names the fourteen that were seen to work. Nobody had ever sent an
#: attachment through this gateway to a real model before that run — the catalogue entry did not
#: declare the capability, so every one of these would have been refused at the gate.
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
#: absent on purpose: "does this look like Markdown" has no answer, and inventing one would refuse
#: valid content to no benefit.
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

    The body ceiling (`AIRA_MAX_REQUEST_BYTES`) already applies and is the outer one; base64
    inflates by a third, so an 8 MiB body carries at most ~6 MiB of document. These bounds sit
    inside it so a refusal says *which* limit was reached rather than only that one was.
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

    Writing a base64 PDF into `request_logs.request_payload` would make each row megabytes, put
    binary the gateway never inspected inside the retention and redaction boundary, and give
    redaction something it cannot process. The description answers everything an audit asks: that
    a document was sent, of what type, how large, in which position — and the digest links repeated
    submissions of the same file without storing it once.
    """
    return {
        "kind": "data",
        "media_type": media_type,
        "bytes": len(data),
        "sha256": sha256(data).hexdigest(),
        "index": index,
    }


#: The key naming a part's media type, in either spelling. **A dict carrying one of these together
#: with `data` is inline binary, wherever it sits** — which is the question, and it used to be
#: asked as a list of wrapper key names instead.
#:
#: That list was `("inlineData", "inline_data")`, the Gemini shape, with a comment saying the KIRA
#: shape "adds its own when it lands". It landed, and nothing was added: the KIRA surface carries
#: the bytes **on the part itself** (`{"mime_type": …, "data": …}`) with no wrapper at all, so no
#: key matched and every attachment went into `request_logs.request_payload` verbatim. Measured on
#: 2026-08-19 — a 5 KB PDF stored whole, on a request that was *refused*, which is this function's
#: own docstring describing what must never happen.
#:
#: Asking about the shape rather than the wrapper covers both surfaces and the third one nobody
#: has written. A key list is a thing to remember; a shape is a thing to recognise.
_MIME_KEYS = ("mimeType", "mime_type")

#: Wrapper keys kept for the Gemini shape, where the datum is the *value* under the key. The
#: recursion below reaches it either way; these are what preserves the wrapper in the stored row.
_INLINE_KEYS = ("inlineData", "inline_data")


def _inline_datum(payload: dict[str, Any]) -> bool:
    """Whether this dict *is* inline binary: a media type and the bytes, together."""
    return any(key in payload for key in _MIME_KEYS) and "data" in payload


def _measure(raw: Any) -> tuple[int, str]:
    """The decoded size and digest of a base64 datum, or `(0, "")` if it is not decodable.

    Decoded **once**: this used to call `b64decode` twice, for the size and for the digest, which
    is two passes over a document on the write path for one answer.

    Never raises. A payload reaching here has usually been validated on the request path — but a
    *response* payload has not, and an upstream returning something that is not base64 must not
    cost the audit row (the same door as `storable` in the writer).
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

    Applied to every stored payload **before** redaction, and unconditionally — not as a redactor
    implementation, because a deployment that swaps the redactor must not be able to turn this off.
    Stripping bytes is not redaction: redaction masks values *inside* content, this removes content
    that should never have been persisted at all.
    """
    if isinstance(payload, list):
        return [strip_attachments(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    # The part *is* the datum — the KIRA shape. Replaced in place, because there is no wrapper to
    # put a summary under and the surrounding part is what a reader is looking at.
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
            # The stored size is the *decoded* one where we can compute it, because that is the
            # figure an audit compares against a limit — base64 is a third larger and comparing it
            # to a byte bound would be quietly wrong.
            size, digest = _measure(raw)
            return {
                **{k: strip_attachments(v) for k, v in payload.items() if k != key},
                key: {"kind": "data", "media_type": media_type, "bytes": size, "sha256": digest},
            }
    return {key: strip_attachments(value) for key, value in payload.items()}
