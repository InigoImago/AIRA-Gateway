"""The response schema: parsing, bounds, and the two wire dialects (FRD-112).

The schema is caller-supplied structure that we forward and never execute, so the bounds in
:class:`SchemaBounds` *are* the gateway's entire exposure to it. They are therefore tested at their
boundaries rather than somewhere comfortably past them.
"""

from __future__ import annotations

import json

import pytest

from aira_gateway.core.schema import (
    ResponseSchema,
    SchemaBounds,
    SchemaRejected,
    SchemaType,
    parse,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import to_json_schema

# the predecessor's contract's own example — the thing a migrating client actually sends.
RECIPES = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "recipeName": {"type": "STRING"},
            "ingredients": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "propertyOrdering": ["recipeName", "ingredients"],
        "required": ["recipeName"],
    },
}


# == the vocabulary ==============================================================================


def test_the_predecessors_example_parses_and_round_trips() -> None:
    schema = parse(RECIPES)
    assert schema.type is SchemaType.ARRAY
    assert schema.items is not None
    assert schema.items.property_ordering == ["recipeName", "ingredients"]
    # Back out in the wire spelling, which is what the provider will be sent.
    assert schema.to_wire() == RECIPES


@pytest.mark.parametrize("type_name", ["STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT"])
def test_every_supported_type_parses(type_name: str) -> None:
    assert parse({"type": type_name}).type == SchemaType(type_name)


def test_lowercase_types_are_accepted() -> None:
    """A compatibility surface that rejected `"string"` over capitalisation would be refusing the
    thing it exists to accept — the type set is identical either way."""
    assert parse({"type": "string"}).type is SchemaType.STRING


def test_a_type_outside_the_set_is_refused() -> None:
    with pytest.raises(SchemaRejected):
        parse({"type": "TUPLE"})


def test_an_unknown_field_is_named() -> None:
    """A caller sending JSON Schema draft 2020-12 gets the field we did not understand, not a
    best-effort conversion that drops the constraint they cared about."""
    with pytest.raises(SchemaRejected) as caught:
        parse({"type": "OBJECT", "additionalProperties": False})
    assert "additionalProperties" in str(caught.value)


def test_an_unknown_field_deep_inside_is_named_with_its_path() -> None:
    with pytest.raises(SchemaRejected) as caught:
        parse({"type": "OBJECT", "properties": {"a": {"type": "STRING", "$ref": "#/x"}}})
    assert "properties.a" in str(caught.value)


def test_a_schema_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(SchemaRejected):
        parse(["not", "a", "schema"])


def test_a_schema_that_cannot_be_serialised_is_refused() -> None:
    with pytest.raises(SchemaRejected):
        parse({"type": "OBJECT", "default": {1, 2}})


# == the bounds ==================================================================================


def test_depth_is_refused_one_level_past_the_bound() -> None:
    bounds = SchemaBounds(max_depth=3)
    ok: dict[str, object] = {
        "type": "ARRAY",
        "items": {"type": "ARRAY", "items": {"type": "STRING"}},
    }
    parse(ok, bounds)

    too_deep = {"type": "ARRAY", "items": ok}
    with pytest.raises(SchemaRejected) as caught:
        parse(too_deep, bounds)
    assert "3 levels" in str(caught.value)


def test_the_property_count_is_refused_at_its_boundary() -> None:
    bounds = SchemaBounds(max_properties=4)
    properties = {f"p{i}": {"type": "STRING"} for i in range(4)}
    parse({"type": "OBJECT", "properties": properties}, bounds)

    properties["p4"] = {"type": "STRING"}
    with pytest.raises(SchemaRejected) as caught:
        parse({"type": "OBJECT", "properties": properties}, bounds)
    assert "5 properties" in str(caught.value)


def test_properties_are_counted_across_the_whole_tree() -> None:
    """A caller splitting two hundred properties over five nested objects is still sending two
    hundred properties; counting per node would let the bound be walked around."""
    nested = {
        "type": "OBJECT",
        "properties": {
            "a": {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}},
            "b": {"type": "OBJECT", "properties": {"y": {"type": "STRING"}}},
        },
    }
    with pytest.raises(SchemaRejected):
        parse(nested, SchemaBounds(max_properties=3))


def test_size_is_bounded_before_the_document_is_parsed() -> None:
    """The parse is what a very large schema is meant to cost us, so the ceiling applies to the
    submitted bytes rather than to whatever survives parsing."""
    huge = {"type": "OBJECT", "description": "x" * 5000}
    with pytest.raises(SchemaRejected) as caught:
        parse(huge, SchemaBounds(max_bytes=1024))
    assert "1024" in str(caught.value)


def test_anyof_variants_are_walked_by_the_bounds() -> None:
    """A branch the counter did not descend into is a bound with a hole in it."""
    schema = {
        "type": "OBJECT",
        "anyOf": [
            {"type": "OBJECT", "properties": {f"p{i}": {"type": "STRING"} for i in range(5)}}
        ],
    }
    with pytest.raises(SchemaRejected):
        parse(schema, SchemaBounds(max_properties=3))


# == identity ====================================================================================


def test_the_digest_is_stable_across_key_order() -> None:
    """The audit records a fingerprint rather than the schema (§6): schemas are large, repetitive,
    and occasionally reveal the caller's data model. It must answer "the same schema?" reliably."""
    first = parse({"type": "OBJECT", "title": "t", "description": "d"})
    second = parse({"description": "d", "title": "t", "type": "OBJECT"})
    assert first.digest() == second.digest()
    assert first.digest() != parse({"type": "OBJECT", "title": "other"}).digest()


# == the Anthropic dialect =======================================================================


def test_the_json_schema_translation_keeps_every_value_constraint() -> None:
    """§5.2: a field with no faithful equivalent is a refusal, not a silent drop. These all have
    one, so a caller who bounded a value must still find it bounded on the other side."""
    schema = parse(
        {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "minLength": 2, "maxLength": 8, "pattern": "^[a-z]+$"},
                "tags": {"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 1},
            },
            "required": ["name"],
        }
    )
    translated = to_json_schema(schema)

    assert translated["type"] == "object"
    assert translated["required"] == ["name"]
    name = translated["properties"]["name"]
    assert (name["minLength"], name["maxLength"], name["pattern"]) == (2, 8, "^[a-z]+$")
    assert translated["properties"]["tags"]["minItems"] == 1
    assert translated["properties"]["tags"]["items"]["type"] == "string"


def test_nullable_becomes_a_type_union() -> None:
    """JSON Schema has no `nullable` flag. Carrying it across as one would be a constraint the
    other side ignores, which is the silent drop §5.2 rules out."""
    assert to_json_schema(parse({"type": "STRING", "nullable": True}))["type"] == ["string", "null"]


def test_ordering_and_examples_are_dropped_and_neither_is_a_constraint() -> None:
    """`propertyOrdering` is a hint about key order in a format where key order carries no
    meaning, and `example` is documentation. Both are safe to omit; nothing else is."""
    translated = to_json_schema(
        parse({"type": "OBJECT", "propertyOrdering": ["a"], "example": {"a": 1}})
    )
    assert "propertyOrdering" not in translated
    assert "example" not in translated


def test_anyof_survives_the_translation() -> None:
    translated = to_json_schema(
        parse({"type": "OBJECT", "anyOf": [{"type": "STRING"}, {"type": "INTEGER"}]})
    )
    assert [variant["type"] for variant in translated["anyOf"]] == ["string", "integer"]


# == the mock's document =========================================================================


def test_the_mock_produces_a_document_that_matches_the_shape() -> None:
    """The mock is the only way the whole path is exercised in CI, so what it returns has to be
    the shape that was asked for rather than a plausible-looking string."""
    from aira_gateway.upstreams.mock import synthesise

    document = synthesise(parse(RECIPES), "prompt")
    assert isinstance(document, list)
    assert list(document[0]) == ["recipeName", "ingredients"]
    assert isinstance(document[0]["ingredients"], list)
    json.dumps(document)  # it must actually serialise


@pytest.mark.parametrize(
    ("schema", "check"),
    [
        ({"type": "INTEGER"}, lambda v: isinstance(v, int)),
        ({"type": "NUMBER"}, lambda v: isinstance(v, float)),
        ({"type": "BOOLEAN"}, lambda v: isinstance(v, bool)),
        ({"type": "STRING", "enum": ["a", "b"]}, lambda v: v == "a"),
        ({"type": "ARRAY"}, lambda v: v == []),
    ],
)
def test_the_mock_honours_each_leaf_type(schema: dict[str, object], check: object) -> None:
    from aira_gateway.upstreams.mock import synthesise

    assert check(synthesise(parse(schema), "abc"))  # type: ignore[operator]


def test_a_schema_model_is_constructible_directly() -> None:
    """The canonical request holds one of these, so it must be usable without going through the
    wire parser — otherwise every internal producer would have to serialise first."""
    schema = ResponseSchema(type=SchemaType.STRING)
    assert schema.to_wire() == {"type": "STRING"}
