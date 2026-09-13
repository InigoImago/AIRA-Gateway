"""Our response schema in the shape the Anthropic Messages API accepts, and what it cannot say.

`ResponseSchema` is Google's vocabulary and wider than this API's, so a schema that works on one
provider is not automatically expressible here (`ADR-0012` §3).
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.schema import ResponseSchema, to_json_schema

#: Constraints this API's structured output does **not** support. A schema using one skips the
#: candidate by name rather than being sent with the constraint dropped.
UNSUPPORTED_SCHEMA_FIELDS = frozenset(
    {"minimum", "maximum", "min_length", "max_length", "min_items", "max_items", "pattern"}
)


def schema_for_anthropic(schema: ResponseSchema) -> dict[str, Any]:
    """Our schema in the shape this provider requires.

    Every object must carry `additionalProperties: false` and list its `required` fields. Both are
    filled in here: a Gemini-shaped schema is a legitimate request, and this is a translation.
    """
    tightened = _tighten(to_json_schema(schema))
    assert isinstance(tightened, dict)  # a schema's root is an object by construction
    return tightened


def _tighten(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    out = {key: _tighten(value) for key, value in node.items()}
    if out.get("type") == "object":
        properties = out.get("properties") or {}
        out["additionalProperties"] = False
        # An absent `required` means "all optional" and is rejected here, so an unqualified object
        # requires all of its properties.
        out.setdefault("required", list(properties))
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _tighten(v) for k, v in out["properties"].items()}
    return out


def schema_refusal(schema: ResponseSchema) -> str | None:
    """Why this dialect cannot express ``schema``, or ``None``.

    Read by the dispatch chain (`ADR-0012` §3) so an inexpressible schema skips the candidate
    instead of being sent with its constraints quietly dropped (`FRD-112`).
    """
    used = sorted(
        field for field in UNSUPPORTED_SCHEMA_FIELDS if getattr(schema, field, None) is not None
    )
    nested = list((schema.properties or {}).values())
    if schema.items is not None:
        nested.append(schema.items)
    nested.extend(schema.any_of or ())
    for child in nested:
        deeper = schema_refusal(child)
        if deeper is not None:
            return deeper
    if not used:
        return None
    return (
        f"this dialect's structured output does not support {', '.join(used)}, and a schema sent "
        "without them would be satisfied by an answer the caller's constraint excludes"
    )
