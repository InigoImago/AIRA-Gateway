"""What the gateway **calls**, as spans (`FRD-117` FR-5).

The inbound request is a server span (`app.redact_span_query`); this module covers the outbound
half — the model call and the database reads that decide whether it is allowed — so a slow request
shows whether the gateway or the model was slow. `test_outgoing_calls_are_traced.py` asks the
spans, not the wiring.

**The credential this must not leak.** Google AI Studio authenticates with ``?key=<api key>``, and
the httpx instrumentation records the URL verbatim. So every outgoing span is redacted through the
same `SENSITIVE_QUERY_PARAMS` definition as the inbound span and the access log (`ADR-0007`):

- the hook is given **twice** — httpx silently drops a sync hook for `AsyncClient`, which every
  adapter here uses, so a sync-only hook looks like redaction and redacts nothing;
- it redacts **every** string attribute carrying a query string, not the one attribute the
  semantic conventions name today, which an upgrade may rename.

Database spans carry the statement with its placeholders and never the bound values (`FRD-117`
§5.3), which `test_a_database_read_appears_as_a_span_carrying_no_bound_value` keeps true.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.logging import get_logger
from aira_common.observability import model_call, model_call_attributes, redact_url_query

#: Why the gateway is talking to a model. A closed set, because it is what a reader groups by —
#: "what share of my spend is the pipeline" — and two spellings of one purpose would split it.
MODEL_CALL_PURPOSES = frozenset({"serve", "pipeline", "embed", "probe"})

_log = get_logger("aira_gateway.telemetry")


@dataclass(frozen=True, slots=True)
class OutgoingCallsTraced:
    """Which of the two halves this process actually instrumented.

    `False` for httpx means *already instrumented in this process* — the ordinary case for a second
    `create_app` in one test session, never a failure.
    """

    http: bool
    database: bool


def redact_client_span_url(span: Any, _info: Any = None) -> None:
    """Keep a credential-bearing query parameter out of an **outgoing** call's span."""
    if span is None or not span.is_recording():
        return
    for key, value in list((getattr(span, "attributes", None) or {}).items()):
        if not isinstance(value, str):
            continue
        safe = redact_url_query(value)
        if safe != value:
            span.set_attribute(key, safe)


def describe_model_call(span: Any, _info: Any = None) -> None:
    """Say who a **model call** was made for, on the span of the call itself (`FRD-619`).

    A tracing backend joins the call to its request span; a SIEM (`FRD-618`) correlates flat
    records by field and cannot, so the call's record would arrive anonymous. Only inside
    `model_call`: every other outgoing request (JWKS, Vault, Kafka probe) gains no caller.
    Names, never content (`FRD-615`).
    """
    if span is None or not span.is_recording():
        return
    carried = model_call_attributes()
    if carried is None:
        return
    for key, value in carried:
        span.set_attribute(key, value)


def prepare_client_span(span: Any, info: Any = None) -> None:
    """Both halves of what this gateway does to an outgoing span: redact, then identify."""
    redact_client_span_url(span, info)
    describe_model_call(span, info)


async def prepare_client_span_async(span: Any, info: Any = None) -> None:
    """The same hook, as a coroutine — see the module docstring for why both are needed."""
    prepare_client_span(span, info)


async def redact_client_span_url_async(span: Any, info: Any = None) -> None:
    """The redaction alone, as a coroutine: the narrower guarantee a test asks for by itself."""
    redact_client_span_url(span, info)


@contextmanager
def model_call_span(model: str, *, purpose: str) -> Iterator[None]:
    """Mark a block that calls a model, so its client span says which model and why (`FRD-619`).

    Wraps the **call**, not the request: a fallback chain enters it once per attempt, so each tried
    model gets its own record. `test_every_model_call_says_what_it_is.py` fails on an upstream
    invocation outside one of these.
    """
    if purpose not in MODEL_CALL_PURPOSES:
        raise ValueError(f"unknown model call purpose: {purpose!r}")
    with model_call({"aira.model": model, "aira.model_call.purpose": purpose}):
        yield


async def model_call_chunks[Chunk](
    model: str, chunks: AsyncIterator[Chunk], *, purpose: str = "serve"
) -> AsyncIterator[Chunk]:
    """The same mark around a **stream**, in force for the iteration.

    A streaming upstream issues its HTTP request on the first ``__anext__``, not when the iterator
    is built. An async generator shares its consumer's context, so the value set here is what the
    surface's loop sees, reset when the stream is exhausted or closed.
    """
    with model_call_span(model, purpose=purpose):
        async for chunk in chunks:
            yield chunk


def instrument_outgoing_calls(engine: AsyncEngine | None = None) -> OutgoingCallsTraced:
    """Trace the calls this process makes: HTTP through httpx, SQL through ``engine``.

    Imported inside the function so a gateway with telemetry off never loads the packages.
    ``engine`` is passed explicitly: `db/base.py` imports `create_async_engine` by name, so the
    library's ``create_engine`` wrapper never sees this project's engines.
    """
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    httpx_instrumentor = HTTPXClientInstrumentor()
    http = not httpx_instrumentor.is_instrumented_by_opentelemetry
    if http:
        httpx_instrumentor.instrument(
            request_hook=prepare_client_span,
            async_request_hook=prepare_client_span_async,
        )

    sqlalchemy_instrumentor = SQLAlchemyInstrumentor()
    database = engine is not None and not sqlalchemy_instrumentor.is_instrumented_by_opentelemetry
    if database:
        # One engine per process. The instrumentor refuses a second `instrument()` call, so a
        # second engine would need `engines=[...]` here; this reports `False` rather than pretend.
        sqlalchemy_instrumentor.instrument(engine=engine.sync_engine if engine else None)

    _log.info("outgoing_calls_instrumented", http=http, database=database)
    return OutgoingCallsTraced(http=http, database=database)
