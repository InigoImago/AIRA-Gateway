"""Our response schema in the shape the Anthropic Messages API accepts, and what it cannot say.

`ResponseSchema` is Google's vocabulary and wider than this API's, so a schema that works on one
provider is not automatically expressible here (`ADR-0012` §3).
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.schema import ResponseSchema, SchemaType, to_json_schema

#: Constraints this API's structured output does **not** support. A schema using one skips the
#: candidate by name rather than being sent with the constraint dropped. `min_items` is checked
#: apart: 0 and 1 are supported, anything above is not.
UNSUPPORTED_SCHEMA_FIELDS = frozenset(
    {
        "minimum",
        "maximum",
        "min_length",
        "max_length",
        "max_items",
        "min_properties",
        "max_properties",
        "pattern",
    }
)

#: The `minItems` values this API supports.
SUPPORTED_MIN_ITEMS = frozenset({0, 1})

#: The string formats this API's structured output supports; any other is its 400.
SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)

#: Formats our OpenAPI vocabulary uses that state no constraint JSON Schema could check: the width
#: of a number, and `enum` on a string, whose values are the `enum` beside it. Left out of the
#: schema sent here, which refuses any format outside its list — nothing the answer must satisfy is
#: lost with them.
_DESCRIPTIVE_FORMATS = frozenset({"int32", "int64", "float", "double", "enum"})

#: A tool that declares no parameters: an object with nothing in it.
_NO_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}


def schema_for_anthropic(schema: ResponseSchema) -> dict[str, Any]:
    """Our schema in the shape this provider requires.

    Every object must carry `additionalProperties: false` and list its `required` fields. Both are
    filled in here: a Gemini-shaped schema is a legitimate request, and this is a translation.
    """
    tightened = _tighten(to_json_schema(schema))
    assert isinstance(tightened, dict)  # a schema's root is an object by construction
    return tightened


def tool_input_schema(parameters: ResponseSchema | None) -> tuple[dict[str, Any], bool]:
    """A tool's `input_schema`, and whether it can be sent **strict** without changing the tool.

    Strict tool use has the provider guarantee the arguments match, and accepts only the shape
    `schema_for_anthropic` produces: every object closed and listing what it requires, no
    constraint outside this API's vocabulary. A declaration that already says which properties it
    requires, and keeps its objects closed, loses nothing in that translation and is sent strict.
    Any other is sent as written, without `strict`: the guarantee is this gateway's addition — the
    caller asked for none — and buying it by making an optional argument mandatory, or by dropping
    a constraint, would change the tool the caller declared. A strict schema the provider refuses
    would fail every request carrying the tool.
    """
    if parameters is None:
        no_parameters = _tighten(_NO_PARAMETERS)
        assert isinstance(no_parameters, dict)
        return no_parameters, True
    if schema_refusal(parameters) is None and _qualified(parameters):
        return schema_for_anthropic(parameters), True
    return to_json_schema(parameters), False


def _qualified(schema: ResponseSchema) -> bool:
    """Whether every object in ``schema`` says which properties it requires and admits no others."""
    if schema.type is SchemaType.OBJECT and (
        schema.required is None or schema.additional_properties is True
    ):
        return False
    nested = list((schema.properties or {}).values())
    if schema.items is not None:
        nested.append(schema.items)
    nested.extend(schema.any_of or ())
    return all(_qualified(child) for child in nested)


def _is_object(node_type: Any) -> bool:
    """An object type, including the `["object", "null"]` union a nullable object becomes."""
    return node_type == "object" or (isinstance(node_type, list) and "object" in node_type)


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        # `anyOf` keeps its variants in a list; an object among them is closed like any other.
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _tighten(value) for key, value in node.items()}
    if out.get("format") in _DESCRIPTIVE_FORMATS:
        del out["format"]
    if _is_object(out.get("type")):
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
    if schema.min_items is not None and schema.min_items not in SUPPORTED_MIN_ITEMS:
        used.append(f"min_items of {schema.min_items} (only 0 and 1)")
    if (
        schema.format
        and schema.format not in _DESCRIPTIVE_FORMATS
        and schema.format not in SUPPORTED_STRING_FORMATS
    ):
        used.append(f"the format '{schema.format}'")
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
