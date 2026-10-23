"""How this plane reads a JSON body.

**No deeper than the caller can make the decoder recurse safely, and no larger in structure than
a request needs.** DRF's `JSONParser` hands the document to `json.load`, whose decoder recurses as
deep as the **caller** nests, and the resulting `RecursionError` is not a `ValueError` — nothing
would catch it; and every value it builds is work the caller chose. Both bounds are
`aira_common.nesting`'s, the same constants the gateway reads, so the two planes cannot disagree
about what a body may be. Registered as the first `DEFAULT_PARSER_CLASSES` entry, so no endpoint
has to remember it.

**Only the encodings a JSON text can be written in.** The charset comes from the caller's
`Content-Type`, and Django keeps any name `codecs.lookup` accepts — which includes byte-to-byte
transforms such as `bz2`, `zlib`, `base64` and `rot13`. Decoded with one of those, a few hundred
bytes expand to hundreds of megabytes before any bound applies, and garbage raises errors no
handler expects. RFC 8259 names UTF-8 (and its predecessor UTF-16/32); anything else is a `415`
naming the charset.

**And no more of it than this plane will hold** — but not here. DRF hands this parser the body
through `request.body`, where Django applies `DATA_UPLOAD_MAX_MEMORY_SIZE` before reading it, so a
body the proxy in front would have let through is still bounded when there is no proxy in front.
`apps.api.exceptions` answers that refusal as the `413` it is.

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
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser

from aira_common.nesting import MAX_JSON_DEPTH, MAX_JSON_VALUES, Exceeded, exceeded

NUL = "\x00"

#: The refusal of a body carrying U+0000, which no column of this plane can store.
NUL_REFUSAL = "The JSON body contains a NUL character (\\u0000), which no stored value can hold."

# The codec names `codecs.lookup` normalises the Unicode encodings to.
JSON_ENCODINGS = frozenset(
    {"utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"}
)


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
        raw = stream.read()
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
        bound = exceeded(text.encode("utf-8", "surrogatepass"))
        if bound is Exceeded.DEPTH:
            raise ParseError(f"JSON body nests deeper than {MAX_JSON_DEPTH} levels.")
        if bound is Exceeded.VALUES:
            raise ParseError(f"JSON body holds more than {MAX_JSON_VALUES} values.")
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
