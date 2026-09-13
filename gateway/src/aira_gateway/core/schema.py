"""The schema a caller may constrain an answer to (`FRD-112`).

Modelled explicitly rather than carried as ``dict[str, Any]``: the bounds need something to count
(the structure and its recursion are caller-controlled), an unknown field becomes an error naming
the field at our boundary, and both surfaces map onto one model.

**The schema is forwarded, never executed.** Validating a response against it would run
caller-supplied ``pattern`` regexes over provider output on the hot path — the exposure `ADR-0007`
rejected for pipeline configuration. The gateway's exposure is bounded by the counting in
:func:`parse` alone, and counting cannot backtrack.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

#: Fields that exist with the same meaning in our OpenAPI-3.0 vocabulary and in JSON Schema — what
#: Anthropic's ``input_schema`` and OpenAI's ``response_format`` want — so translating them is
#: faithful, the condition `FRD-112` §5.2 sets. Kept beside the model rather than in an adapter, so
#: no dialect imports from another.
_JSON_SCHEMA_FIELDS = (
    "description",
    "title",
    "pattern",
    "default",
    "minimum",
    "maximum",
    "enum",
    "required",
)
_JSON_SCHEMA_ALIASES = {
    "min_length": "minLength",
    "max_length": "maxLength",
    "min_items": "minItems",
    "max_items": "maxItems",
    "min_properties": "minProperties",
    "max_properties": "maxProperties",
    "additional_properties": "additionalProperties",
}


class SchemaRejected(Exception):
    """A response schema this gateway will not forward, and why in words a caller can act on."""


@dataclass(frozen=True, slots=True)
class SchemaBounds:
    """FR-3. Each conservative, each refused with a message naming the bound it broke."""

    max_bytes: int = 32 * 1024
    max_depth: int = 8
    max_properties: int = 256


class SchemaType(StrEnum):
    """The predecessor's type set, which is Google's."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ARRAY = "ARRAY"
    OBJECT = "OBJECT"


class ResponseSchema(BaseModel):
    """One node of an OpenAPI-3.0-flavoured schema.

    ``extra="forbid"`` is the point: a caller sending JSON Schema 2020-12 gets an error naming the
    field we did not understand, rather than a conversion that drops the constraint they cared
    about (`FRD-112` §2).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: SchemaType
    properties: dict[str, ResponseSchema] | None = None
    items: ResponseSchema | None = None
    property_ordering: list[str] | None = Field(default=None, alias="propertyOrdering")
    required: list[str] | None = None
    enum: list[str] | None = None
    format: str | None = None
    description: str | None = None
    title: str | None = None
    pattern: str | None = None
    nullable: bool | None = None
    default: Any | None = None
    example: Any | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, alias="minLength")
    max_length: int | None = Field(default=None, alias="maxLength")
    min_items: int | None = Field(default=None, alias="minItems")
    max_items: int | None = Field(default=None, alias="maxItems")
    min_properties: int | None = Field(default=None, alias="minProperties")
    max_properties: int | None = Field(default=None, alias="maxProperties")
    any_of: list[ResponseSchema] | None = Field(default=None, alias="anyOf")
    #: Whether keys beyond ``properties`` are allowed. Part of the shared vocabulary — the same in
    #: OpenAPI 3.0 and JSON Schema — and what every "strict" structured-output client sends.
    additional_properties: bool | None = Field(default=None, alias="additionalProperties")

    @field_validator("type", mode="before")
    @classmethod
    def _accept_either_case(cls, value: Any) -> Any:
        """``"string"`` and ``"STRING"`` are the same request; the type set is identical."""
        return value.upper() if isinstance(value, str) else value

    def to_wire(self) -> dict[str, Any]:
        """The Gemini/OpenAPI form: camelCase aliases, nothing that was not set."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    def digest(self) -> str:
        """A stable fingerprint, for the audit row and the span (`FRD-112` §6).

        The schema itself is **not** persisted — large, repetitive, and occasionally revealing the
        caller's data model. A digest answers "is this the same schema as that one".
        """
        canonical = json.dumps(self.to_wire(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def parse(raw: Any, bounds: SchemaBounds | None = None) -> ResponseSchema:
    """Validate and bound a caller-supplied schema, or raise :class:`SchemaRejected`.

    The size ceiling applies to the **submitted** document, before parsing — the parse is what a
    very large one is meant to cost us.
    """
    bounds = bounds or SchemaBounds()
    if not isinstance(raw, dict):
        raise SchemaRejected("The response schema must be an object.")

    try:
        encoded = len(json.dumps(raw).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SchemaRejected("The response schema is not representable as JSON.") from exc
    if encoded > bounds.max_bytes:
        raise SchemaRejected(
            f"The response schema is {encoded} bytes, above the {bounds.max_bytes} accepted."
        )

    try:
        schema = ResponseSchema.model_validate(raw)
    except ValidationError as exc:
        raise SchemaRejected(_first_problem(exc)) from exc

    properties = _measure(schema, 1, bounds)
    if properties > bounds.max_properties:
        raise SchemaRejected(
            f"The response schema declares {properties} properties, above the "
            f"{bounds.max_properties} accepted."
        )
    return schema


def _measure(node: ResponseSchema, depth: int, bounds: SchemaBounds) -> int:
    """Depth and total property count in one walk; returns the properties counted below ``node``.

    Depth is checked *during* the walk, so a very deep schema is refused before it is walked whole.
    """
    if depth > bounds.max_depth:
        raise SchemaRejected(f"The response schema nests deeper than {bounds.max_depth} levels.")
    counted = 0
    for child in (node.properties or {}).values():
        counted += 1 + _measure(child, depth + 1, bounds)
    if node.items is not None:
        counted += _measure(node.items, depth + 1, bounds)
    for variant in node.any_of or ():
        counted += _measure(variant, depth + 1, bounds)
    return counted


def _first_problem(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    kind = first.get("type", "")
    if kind == "extra_forbidden":
        return (
            f"'{location}' is not a field of the supported schema vocabulary. "
            "It is refused rather than dropped, because a constraint that is silently ignored "
            "produces an answer that is wrong in a way nothing about the response would show."
        )
    return f"{location}: {first.get('msg', 'invalid')}".strip(": ")


def to_json_schema(schema: ResponseSchema) -> dict[str, Any]:
    """OpenAPI 3.0 subset → JSON Schema, faithfully.

    Two fields are not carried, and neither is a constraint: ``example`` is documentation, and
    ``propertyOrdering`` is a hint about key order in a format where key order means nothing.
    """
    out: dict[str, Any] = {"type": str(schema.type).lower()}
    if schema.nullable:
        # JSON Schema expresses nullability as a type union, not as a flag.
        out["type"] = [out["type"], "null"]
    if schema.format:
        out["format"] = schema.format
    for field in _JSON_SCHEMA_FIELDS:
        value = getattr(schema, field)
        if value is not None:
            out[field] = value
    for field, alias in _JSON_SCHEMA_ALIASES.items():
        value = getattr(schema, field)
        if value is not None:
            out[alias] = value
    if schema.properties:
        out["properties"] = {
            name: to_json_schema(child) for name, child in schema.properties.items()
        }
    if schema.items is not None:
        out["items"] = to_json_schema(schema.items)
    if schema.any_of:
        out["anyOf"] = [to_json_schema(variant) for variant in schema.any_of]
    return out
