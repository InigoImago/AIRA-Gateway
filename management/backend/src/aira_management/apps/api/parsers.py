"""How this plane reads a JSON body.

DRF's `JSONParser` hands the document to `json.load`, whose decoder recurses as deep as the
**caller** nests — and the resulting `RecursionError` is not a `ValueError`, so it surfaced as a
500. The bound is `aira_common.nesting`'s, the same constant the gateway reads, so the two planes
cannot disagree about what a body may be. Registered as the first `DEFAULT_PARSER_CLASSES` entry,
so no endpoint has to remember it.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

from aira_common.nesting import MAX_JSON_DEPTH, nests_deeper_than


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
        encoding: str = context.get("encoding", settings.DEFAULT_CHARSET)
        raw = stream.read()
        # Measured on the bytes as they arrived, so a body about to be refused is never decoded.
        # A test client that hands over `str` is encoded back, once.
        measurable = raw if isinstance(raw, bytes) else str(raw).encode(encoding, "surrogatepass")
        if nests_deeper_than(measurable, MAX_JSON_DEPTH):
            raise ParseError(f"JSON body nests deeper than {MAX_JSON_DEPTH} levels.")
        try:
            text = raw if isinstance(raw, str) else codecs.decode(raw, encoding)
            return json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError(f"JSON parse error - {exc}") from exc
