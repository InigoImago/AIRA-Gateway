"""The schema a caller may constrain an answer to (FRD-112).

Modelled explicitly rather than carried as ``dict[str, Any]``, for three reasons in this order:

1. The bounds in FR-3 need something to count. A schema is caller-supplied *structure* and its
   recursion is caller-controlled.
2. An unknown field becomes an error naming the field, at our boundary, instead of a provider
   error naming nothing useful.
3. Both surfaces then map onto one model rather than each inventing their own — which is the
   whole reason a canonical core exists.

**The schema is forwarded, never executed.** Re-validating a response against it would mean
running caller-supplied ``pattern`` regexes over provider output on the hot path, which is the
exposure `ADR-0007` already rejected for pipeline configuration, arriving by a different door.
The gateway's exposure is therefore bounded by the counting in :func:`parse` alone, and counting
cannot backtrack.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SchemaRejected(Exception):
    """A response schema this gateway will not forward, and why in words a caller can act on."""


class SchemaType(StrEnum):
    """The predecessor's type set (`kira_api.md` §4.5), which is Google's."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ARRAY = "ARRAY"
    OBJECT = "OBJECT"


class ResponseSchema(BaseModel):
    """One node of an OpenAPI-3.0-flavoured schema.

    ``extra="forbid"`` is the point of the model: a caller sending JSON Schema draft 2020-12 gets
    an error naming the field we did not understand, rather than a best-effort conversion that
    drops the constraint they cared about (`FRD-112` §2).
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

    @field_validator("type", mode="before")
    @classmethod
    def _accept_either_case(cls, value: Any) -> Any:
        """``"string"`` and ``"STRING"`` are the same request.

        Google's wire format is uppercase and plenty of client code carries a lowercase JSON
        Schema habit. Normalising is not a best-effort conversion — the type set is identical
        either way — whereas refusing over capitalisation would be a compatibility surface that
        rejects the thing it exists to accept.
        """
        return value.upper() if isinstance(value, str) else value

    def to_wire(self) -> dict[str, Any]:
        """The Gemini/OpenAPI form: camelCase aliases, nothing that was not set."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    def digest(self) -> str:
        """A stable fingerprint, for the audit row and the span (`FRD-112` §6).

        The schema itself is **not** persisted: schemas are large, repetitive, and occasionally
        reveal the caller's internal data model. A digest answers "is this the same schema as
        that one" — which is every question the audit actually asks of it.
        """
        canonical = json.dumps(self.to_wire(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SchemaBounds:
    """FR-3. Each conservative, each refused with a message naming the bound it broke."""

    max_bytes: int = 32 * 1024
    max_depth: int = 8
    max_properties: int = 256


def _measure(node: ResponseSchema, depth: int, bounds: SchemaBounds) -> int:
    """Depth and total property count, in one walk. Returns the properties counted below ``node``.

    Depth is checked *during* the walk rather than after it: a schema nested ten thousand deep
    would otherwise be fully parsed before anyone objected, and the parse is the expensive part.
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


def parse(raw: Any, bounds: SchemaBounds | None = None) -> ResponseSchema:
    """Validate and bound a caller-supplied schema, or raise :class:`SchemaRejected`.

    The size ceiling is applied to the **submitted** document before parsing, because the parse is
    what a very large one is meant to cost us.
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
