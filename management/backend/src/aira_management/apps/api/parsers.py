"""How this plane reads a JSON body.

**No deeper than the caller can make the decoder recurse safely.** DRF's `JSONParser` hands the
document to `json.load`, whose decoder recurses as deep as the **caller** nests, and the resulting
`RecursionError` is not a `ValueError` — nothing would catch it. The bound is
`aira_common.nesting`'s, the same constant the gateway reads, so the two planes cannot disagree
about what a body may be. Registered as the first `DEFAULT_PARSER_CLASSES` entry, so no endpoint
has to remember it.

**Only the encodings a JSON text can be written in.** The charset comes from the caller's
`Content-Type`, and Django keeps any name `codecs.lookup` accepts — which includes byte-to-byte
transforms such as `bz2`, `zlib`, `base64` and `rot13`. Decoded with one of those, a few hundred
bytes expand to hundreds of megabytes before any bound applies, and garbage raises errors no
handler expects. RFC 8259 names UTF-8 (and its predecessor UTF-16/32); anything else is a `415`
naming the charset.

**And no more of it than this plane will hold.** The body is read up to
`DATA_UPLOAD_MAX_MEMORY_SIZE` — Django's own ceiling for a request body, which DRF's stream
bypasses — so a body the proxy in front would have let through is still bounded when there is no
proxy in front.

**No NUL character in any string.** PostgreSQL's text and `jsonb` types cannot hold U+0000, so a
`\u0000` anywhere in a document — a pipeline instruction, a declaration, a key — is a `DataError`
at the save, a `500` for a value the caller sent. Refused here, once, for every body; the path and
query string are refused by `apps.api.middleware`.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException, ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser

from aira_common.nesting import MAX_JSON_DEPTH, nests_deeper_than

NUL = "\x00"

#: The refusal of a body carrying U+0000, which no column of this plane can store.
NUL_REFUSAL = "The JSON body contains a NUL character (\\u0000), which no stored value can hold."

# The codec names `codecs.lookup` normalises the Unicode encodings to.
JSON_ENCODINGS = frozenset(
    {"utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"}
)


class BodyTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "The request body is larger than this service accepts."
    default_code = "payload_too_large"


def json_encoding(declared: str) -> str:
    """The codec to read a body declared as `declared`, or a `415` naming it."""
    try:
        name = codecs.lookup(declared).name
    except LookupError:
        name = ""
    if name not in JSON_ENCODINGS:
        raise UnsupportedMediaType(
            f"application/json; charset={declared}",
            detail=f"A JSON body is read as UTF-8, UTF-16 or UTF-32, not as '{declared}'.",
        )
    return name


class BoundedJSONParser(JSONParser):
    """`JSONParser`, refusing a document too deeply nested to decode, with a `400` naming the
    bound: the value is the caller's, so the refusal is theirs to act on."""

    def parse(
        self,
        stream: Any,
        media_type: str | None = None,
        parser_context: Mapping[str, Any] | None = None,
    ) -> Any:
        context: Mapping[str, Any] = parser_context or {}
        encoding = json_encoding(context.get("encoding", settings.DEFAULT_CHARSET))
        limit: int | None = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        raw = stream.read() if limit is None else stream.read(limit + 1)
        if limit is not None and len(raw) > limit:
            raise BodyTooLarge(f"The request body is larger than {limit} bytes.")
        try:
            # A test client may hand over `str` already.
            text = raw if isinstance(raw, str) else codecs.decode(raw, encoding)
        except UnicodeDecodeError as exc:
            raise ParseError(f"JSON parse error - {exc}") from exc
        # **Measured as UTF-8, whatever it arrived as.** The depth scan reads bytes and knows
        # UTF-8's shape only; in UTF-16 a character such as `Ģ` is the bytes `22 01`, which it read
        # as a closing quote, after which every bracket counted as string text and a body of any
        # depth passed — to a `RecursionError` in the decoder. Decoding a Unicode text first costs
        # one linear pass and cannot expand it.
        if nests_deeper_than(text.encode("utf-8", "surrogatepass"), MAX_JSON_DEPTH):
            raise ParseError(f"JSON body nests deeper than {MAX_JSON_DEPTH} levels.")
        try:
            document = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"JSON parse error - {exc}") from exc
        if holds_nul(document):
            raise ParseError(NUL_REFUSAL)
        return document


def holds_nul(document: Any) -> bool:
    """Whether any string in ``document`` — a value or a key — contains U+0000.

    A walk with an explicit stack: the depth is already bounded, and a walk that cannot recurse
    cannot be the next `RecursionError`.
    """
    pending: list[Any] = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if NUL in value:
                return True
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False
