"""The predecessor's rule for a function's ``parameters`` (`FRD-146` FR-1).

Stricter than the JSON Schema the Gemini surface forwards, and checked on this surface only: a
declaration the predecessor refuses is refused here with its code, so a client written against it
meets the same answer. Every level of the schema is held to the same rule — a known keyword set, a
``type`` that is one of six, ``items`` exactly where it belongs. The size and depth bounds every
schema has still apply after this (`core.json_schema`).
"""

from __future__ import annotations

from typing import Any

#: The keywords the predecessor accepts at any level. Anything else is refused, not dropped: a
#: constraint left out silently is a function called with values its author excluded.
KEYWORDS = frozenset(
    {
        "$ref",
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "nullable",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)

TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})


def fault(schema: Any, where: str = "parameters") -> str | None:
    """What is wrong with ``schema``, or ``None``. The first fault, named by its path."""
    if not isinstance(schema, dict):
        return f"{where} is not a schema object"
    unknown = sorted(set(schema) - KEYWORDS)
    if unknown:
        return f"{where} uses {unknown}, which a function's parameters may not"
    kind = schema.get("type")
    if kind not in TYPES:
        return f"{where}.type is {kind!r}; it is one of {sorted(TYPES)}"
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            return f"{where}.properties is not an object"
        for name, child in properties.items():
            if not name:
                return f"{where}.properties has an empty name"
            found = fault(child, f"{where}.properties.{name}")
            if found:
                return found
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(
            isinstance(name, str) and name for name in required
        ):
            return f"{where}.required is a list of non-empty names"
        missing = [name for name in required if name not in (properties or {})]
        if missing:
            return f"{where}.required names {missing}, which are not among its properties"
    if kind == "array":
        if "items" not in schema:
            return f"{where} is an array without 'items'"
        found = fault(schema["items"], f"{where}.items")
        if found:
            return found
    elif "items" in schema:
        return f"{where} has 'items' but is not an array"
    for keyword in ("anyOf", "enum"):
        if keyword in schema and (not isinstance(schema[keyword], list) or not schema[keyword]):
            return f"{where}.{keyword} is a non-empty list"
    for index, option in enumerate(schema.get("anyOf") or []):
        found = fault(option, f"{where}.anyOf[{index}]")
        if found:
            return found
    return None
