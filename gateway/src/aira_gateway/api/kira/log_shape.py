"""What a refused request's body *was*, for its log line — never what it *said*.

A log line is outside every control on stored content (a use case's `store_payloads`, redaction,
the retention clock, the recorded read — `ADR-0016`), and the privacy notice promises that no log
line carries the content of a request (`FRD-625`). So the shape keeps a **name only where the
contract defines one**, and walks only into the objects the contract defines: the request and its
turns, their parts, the functions offered, the thinking setting, the records to rank — read off the
request models themselves, so the walk cannot drift from what is parsed.

Everything else is its kind and size, and in particular every field whose value is the caller's own
structure: what a function was called with and what it returned, an error it reported, the model's
opaque state, a JSON Schema whose property names are the caller's data model. Those are objects
keyed by data — a patient's name, an e-mail address, a diagnosis as a property — so their keys are
content and are never printed. A field this surface does not model keeps its name, as the response
header names it (`headers.is_nameable`), and never what it holds.

Bounded: `SHAPE_ENTRIES` entries in all, past which the rest is counted, so a body of a hundred
thousand fields is a short line.
"""

from __future__ import annotations

import functools
from typing import Any, get_args

from pydantic import BaseModel

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.headers import is_nameable

#: How many fields, list entries and parts a refused body's logged shape names. The shape grows
#: with the body, which the caller writes, so it is bounded here; past it the rest is counted.
SHAPE_ENTRIES = 256

#: The model each operation's body is parsed as, so the shape follows the positions it defines.
REQUESTS: dict[str, type[BaseModel]] = {
    "chat": schemas.ChatRequest,
    "streaming-chat": schemas.ChatRequest,
    "embed": schemas.EmbeddingRequest,
    "rerank": schemas.RerankRequest,
}

#: What a field of a request model holds: another request model to walk into, the parts of a
#: content — plain mappings of `schemas.PART_FIELDS` — or a value, described and never walked.
_PARTS = "parts"
type _Holds = type[BaseModel] | str | None

#: A part's names, each holding a value: described, never walked — `arguments`, `result`, `error`
#: and `opaque_metadata` are the caller's own objects.
_PART_NAMES: dict[str, _Holds] = dict.fromkeys(schemas.PART_FIELDS)


def _model_in(annotation: Any) -> type[BaseModel] | None:
    """The request model ``annotation`` holds — itself, optional, or in a list — if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in get_args(annotation):
        found = _model_in(argument)
        if found is not None:
            return found
    return None


@functools.cache
def _fields(model: type[BaseModel]) -> dict[str, _Holds]:
    """Every name ``model`` accepts — its fields and their aliases — and what each holds."""
    known: dict[str, _Holds] = {}
    for name, field in model.model_fields.items():
        holds: _Holds = (
            _PARTS
            if model is schemas.RequestContent and name == "parts"
            else _model_in(field.annotation)
        )
        known[name] = holds
        if field.alias:
            known[field.alias] = holds
    return known


def described(value: Any) -> str:
    """A value by its kind and size: ``str(27)``, ``object(3)``, ``list(5)``, ``number``."""
    if isinstance(value, dict):
        return f"object({len(value)})"
    if isinstance(value, list):
        return f"list({len(value)})"
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, bool):
        return "bool"
    return "null" if value is None else "number"


def body_shape(body: Any, operation: str = "chat") -> Any:
    """``body`` with every value replaced by its kind and size: ``{"text": "str(27)"}``.

    Walks only where ``operation``'s request model defines an object; see the module docstring.
    """
    remaining = SHAPE_ENTRIES

    def entries(value: list[Any], each: Any) -> list[Any]:
        nonlocal remaining
        shown: list[Any] = []
        for index, item in enumerate(value):
            if remaining <= 0:
                shown.append(f"… {len(value) - index} more")
                break
            remaining -= 1
            shown.append(each(item))
        return shown

    def mapping(value: Any, known: dict[str, _Holds], walk: bool) -> Any:
        nonlocal remaining
        if not isinstance(value, dict):
            return described(value)
        shown: dict[str, Any] = {}
        hidden = 0
        for index, (name, item) in enumerate(value.items()):
            if remaining <= 0:
                hidden += len(value) - index
                break
            remaining -= 1
            key = str(name)
            if key not in known and not is_nameable(key):
                # A name the caller chose that cannot be repeated is counted, like the header does.
                hidden += 1
                continue
            # A name the contract does not define holds the caller's own value: described.
            shown[key] = held(item, known.get(key)) if walk else described(item)
        if hidden:
            shown["…"] = f"{hidden} more"
        return shown

    def held(value: Any, holds: _Holds) -> Any:
        if holds is None:
            return described(value)
        if holds == _PARTS:
            if not isinstance(value, list):
                return described(value)
            return entries(value, lambda part: mapping(part, _PART_NAMES, walk=False))
        assert not isinstance(holds, str)
        model = holds
        if isinstance(value, list):
            return entries(value, lambda item: mapping(item, _fields(model), walk=True))
        return mapping(value, _fields(model), walk=True)

    request = REQUESTS.get(operation)
    if request is None:
        return described(body)
    return mapping(body, _fields(request), walk=True)
