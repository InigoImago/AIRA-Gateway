"""A caller's schema written in **JSON Schema**, carried as written (`FRD-143`).

Google's API takes a schema two ways: the OpenAPI-3.0 subset of `parameters` and `responseSchema`,
which `core.schema` parses into its own vocabulary and refuses outside it, and full JSON Schema in
`parametersJsonSchema` and `responseJsonSchema`, which current SDKs send — the AI SDK's Google
provider declares every tool that way since its fourth major version. JSON Schema is also what the
other two dialects take natively (Anthropic's `input_schema`, OpenAI's `parameters` and
`json_schema`), so it needs no translation and gets none: a document translated into a smaller
vocabulary would either drop a constraint or be refused for one the provider accepts.

What this gateway still owns is the **bound**. A JSON Schema document is caller-supplied structure
like any other, so it gets the same ceilings a `responseSchema` gets — bytes, depth, properties —
and the same refusal wording. Everything else is the provider's to accept or refuse, and a
provider's refusal of a schema is a 400 whose reason reaches the caller.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from aira_gateway.core.schema import SchemaBounds, SchemaRejected


class JsonSchemaDocument(BaseModel):
    """A bounded JSON Schema document, as the caller wrote it."""

    model_config = ConfigDict(frozen=True)

    document: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        """The document as sent — a copy, so a dialect cannot change the request it came from."""
        copied: dict[str, Any] = json.loads(json.dumps(self.document))
        return copied

    def to_json(self) -> str:
        """Compact JSON, for what the reservation counts as input."""
        return json.dumps(self.document, separators=(",", ":"), ensure_ascii=False)

    def digest(self) -> str:
        """A stable fingerprint, as `ResponseSchema.digest` gives one (`FRD-112` §6)."""
        canonical = json.dumps(self.document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def parse_json_schema(
    raw: Any, bounds: SchemaBounds | None = None, *, what: str = "The JSON schema"
) -> JsonSchemaDocument:
    """Bound a caller's JSON Schema document, or raise :class:`SchemaRejected`."""
    bounds = bounds or SchemaBounds()
    if not isinstance(raw, dict):
        raise SchemaRejected(f"{what} must be an object.")
    try:
        encoded = len(json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SchemaRejected(f"{what} is not representable as JSON.") from exc
    if encoded > bounds.max_bytes:
        raise SchemaRejected(f"{what} is {encoded} bytes, above the {bounds.max_bytes} accepted.")
    properties = _measure(raw, 1, bounds, what)
    if properties > bounds.max_properties:
        raise SchemaRejected(
            f"{what} declares {properties} properties, above the {bounds.max_properties} accepted."
        )
    return JsonSchemaDocument(document=raw)


def _measure(node: Any, depth: int, bounds: SchemaBounds, what: str) -> int:
    """Depth over nested objects and lists, and the properties declared below ``node``.

    Every nested object counts as a level, wherever JSON Schema puts it — `properties`, `items`,
    `$defs`, `anyOf` — because the walk that a deep document costs is the same in all of them.
    """
    if depth > bounds.max_depth * 2:
        # Two levels of JSON per schema level: `{"properties": {"a": {...}}}` is one schema level.
        raise SchemaRejected(f"{what} nests deeper than {bounds.max_depth} levels.")
    counted = 0
    if isinstance(node, dict):
        declared = node.get("properties")
        if isinstance(declared, dict):
            counted += len(declared)
        for value in node.values():
            counted += _measure(value, depth + 1, bounds, what)
    elif isinstance(node, list):
        for value in node:
            counted += _measure(value, depth + 1, bounds, what)
    return counted
