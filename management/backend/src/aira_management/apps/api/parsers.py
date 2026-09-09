"""How this plane reads a JSON body.

DRF's `JSONParser` hands the whole document to `json.load`, whose decoder recurses — so how deep
it goes is a decision the **caller** makes. Measured on 2026-09-08: a body nesting ~200 000
levels, well inside Django's own 2.5 MB `DATA_UPLOAD_MAX_MEMORY_SIZE`, raised `RecursionError`
out of the decoder on every endpoint this plane publishes. `RecursionError` is not a `ValueError`,
so nothing caught it and each one answered **500**.

That is the gateway's defect one plane over, and it is here rather than only there because *a
rule restated on a second surface is compared by nothing* (`LESSONS.md` §1): the bound lives in
`aira_common.nesting`, both planes read the same constant, and neither can drift from the other.

Registered as the first `DEFAULT_PARSER_CLASSES` entry rather than swapped in per view, because
the endpoint written next is the one that would not have remembered.
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
    """`JSONParser`, refusing a document too deeply nested to decode without recursing forever.

    A `400` naming the bound, not a `500`: the value is the caller's, so the refusal is theirs to
    act on — the same rule the gateway applies at its own door.
    """

    def parse(
        self,
        stream: Any,
        media_type: str | None = None,
        parser_context: Mapping[str, Any] | None = None,
    ) -> Any:
        context: Mapping[str, Any] = parser_context or {}
        encoding: str = context.get("encoding", settings.DEFAULT_CHARSET)
        raw = stream.read()
        # Bytes wherever the request gives us bytes — the scan is defined on the document as it
        # arrived, and decoding 8 MB before measuring it would be work done for a body about to be
        # refused. A test client that hands over `str` is decoded back, once.
        measurable = raw if isinstance(raw, bytes) else str(raw).encode(encoding, "surrogatepass")
        if nests_deeper_than(measurable, MAX_JSON_DEPTH):
            raise ParseError(f"JSON body nests deeper than {MAX_JSON_DEPTH} levels.")
        try:
            text = raw if isinstance(raw, str) else codecs.decode(raw, encoding)
            return json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError(f"JSON parse error - {exc}") from exc
