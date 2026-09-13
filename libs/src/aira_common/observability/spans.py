"""Span attributes, and who a model call was made for (`FRD-619`).

The httpx instrumentation gives an upstream model call only a method, a URL and a status; who
asked lives on the request span above it. A tracing backend joins the two by parent id, but a SIEM
ingests flat records (`FRD-618`) and cannot. So the identifying half of a request is kept in two
context variables and stamped onto the client span by the gateway's httpx hook:

- `_caller` is set once, when the request is attributed, and holds for the rest of it;
- `_model_call` is set only around an actual upstream model call.

A call the gateway makes for its own reasons (a JWKS refresh, a Vault read) therefore gains
nothing: no span outside a model call is labelled with a caller who did not cause it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.util.types import AttributeValue

#: Attributes ready to stamp on a span: primitives only, no ``None``.
type _Stamp = tuple[tuple[str, AttributeValue], ...]

_caller: ContextVar[_Stamp] = ContextVar("aira_model_call_caller", default=())
_model_call: ContextVar[_Stamp | None] = ContextVar("aira_model_call", default=None)


def _primitives(attributes: Mapping[str, object]) -> _Stamp:
    """The attributes a span can carry: primitives only, no ``None``."""
    return tuple(
        (key, value)
        for key, value in attributes.items()
        if isinstance(value, str | int | float | bool)
    )


def set_span_attributes(attributes: Mapping[str, object]) -> None:
    """Set primitive (non-None) attributes on the current span; no-op if none is recording."""
    span = trace.get_current_span()
    for key, value in _primitives(attributes):
        span.set_attribute(key, value)


def trace_context_fields() -> dict[str, str]:
    """Return ``trace_id``/``span_id`` (hex) for the current span, if one is active."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": trace.format_trace_id(ctx.trace_id),
        "span_id": trace.format_span_id(ctx.span_id),
    }


def attribute_model_calls_to(attributes: Mapping[str, object]) -> None:
    """Remember who this request is for, so a model call it makes can say so.

    Called from one place only — `gateway.auth.attribution.set_attribution`, guarded by
    `test_every_attribution_reaches_the_span.py`. Never reset: the context is per task, and a
    request is a task.
    """
    _caller.set(_primitives(attributes))


@contextmanager
def model_call(attributes: Mapping[str, object]) -> Iterator[None]:
    """Mark the block that talks to a model, and say which model.

    Entered per **attempt**: a fallback chain over three models yields three records naming three
    models. Nested calls replace rather than merge — a classifier call inside a serving request is
    its own model access, and naming the outer model on it would be wrong.
    """
    token = _model_call.set(_primitives(attributes))
    try:
        yield
    finally:
        _model_call.reset(token)


def model_call_attributes() -> _Stamp | None:
    """What to stamp on the current outgoing span, or ``None`` if it is not a model call."""
    call = _model_call.get()
    if call is None:
        return None
    return (*_caller.get(), *call)
