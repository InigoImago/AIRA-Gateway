"""What the gateway **calls**, as spans (`FRD-117` FR-5).

`FRD-001` instrumented the *inbound* half: a request produces a server span, and `FRD-105` puts
the attribution on it. The outbound half — the model call, and the database read that decides
whether it is allowed — was specified in `FRD-117` §5.3 (*"`HTTPXClientInstrumentor` and
`SQLAlchemyInstrumentor`, enabled with the existing OTel switch"*), recorded as delivered in that
FRD's §10a (*"FR-1 through FR-6"*) and in `GAP-ANALYSIS.md` row 21 — and **never written**.
Neither package was even a declared dependency. So a trace of a request that spent nine seconds
inside a model showed one span nine seconds long with nothing underneath it, and *"is the gateway
slow or is the model slow"* — the first question anybody asks of a gateway — was not answerable
from the trace at all.

The shape `LESSONS.md` §7 names: **an FRD that cites a control as an existing fact is not a check
that it exists**, and the more confidently it is referenced in passing the less likely anybody
greps for the reader. The counterpart is `gateway/tests/test_outgoing_calls_are_traced.py`, which
asks the *spans* rather than the wiring.

## The credential this had to not leak

`upstreams/gemini.py` authenticates to Google AI Studio the way the Gemini wire protocol does:
``?key=<api key>`` on every request. The httpx instrumentation records the request URL verbatim
(`redact_url` in the library removes `user:password@` and nothing else), so switching it on
without a hook would have written **this installation's upstream API key into a span attribute on
every model call**, exported to a collector and stored in a trace backend that a different set of
people can read. Measured before this was written.

That is the same hazard `ADR-0007` answered for the *inbound* direction — `redact_span_query` in
`app.py`, and `AccessLogRedaction` for the access log — so it gets the same answer and the same
single definition of what a credential-bearing parameter is (`aira_common.observability`).

**The hook has to be given twice, and only one of the two does the work here.** The instrumentation
selects `async_request_hook` for `AsyncClient` and silently drops it unless it is a coroutine
function (`async_request_hook if iscoroutinefunction(...) else None`), and every upstream adapter
in this gateway uses `AsyncClient`. A sync-only hook therefore looks exactly like a working
redaction and redacts nothing — which is why the test drives an async client and asserts on the
attribute rather than on the call.

**Every attribute, not the one the semantic conventions call the URL today.** The library writes
`http.url` in the default stability mode and `url.full` under the new one, chosen from an
environment variable this deployment does not set and a future release may flip. A hook that names
one of them is a redaction that goes vacuous on an upgrade with nothing failing — `LESSONS.md` §7 —
so this one redacts *whatever string attribute carries a query string*, which cannot be renamed
out of.

## What the database spans carry, precisely

`FRD-117` §5.3 asked for SQLAlchemy "with statement text **hidden**", for a stated reason: *"a
bound parameter can carry a prompt fragment or a subject identifier"*. What the instrumentation
actually records is the statement **with its placeholders** (`WHERE use_case = %(param_1)s`) and
never the parameter values — SQLAlchemy hands the two to the event hook separately and only the
first is read. So the reason the requirement was written for is met, and the sentence describing
the mechanism was not accurate; §5.3 now says what is recorded instead of what is hidden, and
`test_a_database_read_appears_as_a_span_carrying_no_bound_value` is what keeps that true.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.logging import get_logger
from aira_common.observability import model_call, model_call_attributes, redact_url_query

_log = get_logger("aira_gateway.telemetry")


@dataclass(frozen=True, slots=True)
class OutgoingCallsTraced:
    """Which of the two halves this process actually instrumented.

    Returned rather than logged only, so a caller can say so and a test can assert it. `False`
    for httpx means *already instrumented in this process*, which is the ordinary case for a
    second `create_app` in one test session — never a failure.
    """

    http: bool
    database: bool


def redact_client_span_url(span: Any, _info: Any = None) -> None:
    """Keep a credential-bearing query parameter out of an **outgoing** call's span.

    The mirror of `app.redact_span_query`, which does this for the inbound request, through the
    same `SENSITIVE_QUERY_PARAMS` list: one definition of *what is a credential in a URL*, read by
    the access log, the server span and now the client span (`LESSONS.md` §1 — a rule restated on
    a second surface is a rule that differs on one of them).
    """
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

    The httpx instrumentation writes what an HTTP client knows: a method, a status, a URL. For an
    outgoing call to a model that is three attributes, and none of them answers *who asked*, *for
    which use case*, or — on an OpenAI-compatible upstream, where the model travels in the request
    body — *which model*. Measured on 2026-09-04 against a live delivery feed: 3 attributes on the
    model call, 29 on the request span above it.

    In a **tracing** backend that costs nothing, because the two spans share a trace and the
    backend joins them. The second destination (`FRD-618`) is not a tracing backend: a SIEM ingests
    flat events and correlates by field, the forwarding filter keeps these child spans on purpose
    so that *"one record per API call and per model call"* holds, and the model-call record was
    arriving anonymous. A join a consumer cannot perform is a field it does not have.

    **Only inside `model_call`.** `model_call_attributes` returns ``None`` for every other outgoing
    request this process makes — a JWKS refresh, a Vault read, a Kafka health probe — so those keep
    the three attributes they had and gain no caller who did not cause them.

    Names, never content (`FRD-615`): a subject, a use case, a credential's identifier, a model, a
    provider. The prompt is not here and must not be.
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
    """The redaction alone, as a coroutine. Kept because it is the narrower guarantee and the
    test that proves the credential never reaches a span asks for exactly that."""
    redact_client_span_url(span, info)


#: Why the gateway is talking to a model. Declared as a closed set rather than passed as free
#: text, because this is the field a reader groups by: *"what share of my spend is the pipeline"*
#: is a question about `pipeline` vs `serve`, and it stops being answerable the moment two call
#: sites spell the same purpose differently.
MODEL_CALL_PURPOSES = frozenset({"serve", "pipeline", "embed", "probe"})


@contextmanager
def model_call_span(model: str, *, purpose: str) -> Iterator[None]:
    """Mark a block that calls a model, so its client span says which model and why (`FRD-619`).

    Wraps the **call**, not the request: a fallback chain enters this once per attempt, so three
    tried models produce three records naming three models rather than three records naming the
    one that eventually answered.

    `test_every_model_call_says_what_it_is.py` fails on an upstream invocation that is not inside
    one of these — the same shape as `test_surface_layering.py`, and for the same reason. There are
    seven such call sites across dispatch, two streaming surfaces, two embedding surfaces, the
    pipeline classifiers and the incident probe; a rule that seven sites must remember is a rule
    six of them keep.
    """
    if purpose not in MODEL_CALL_PURPOSES:
        raise ValueError(f"unknown model call purpose: {purpose!r}")
    with model_call({"aira.model": model, "aira.model_call.purpose": purpose}):
        yield


async def model_call_chunks[Chunk](
    model: str, chunks: AsyncIterator[Chunk], *, purpose: str = "serve"
) -> AsyncIterator[Chunk]:
    """The same mark around a **stream**, without re-indenting the loop that reads it.

    A streaming upstream issues its HTTP request on the first ``__anext__``, not when the iterator
    is built, so the mark has to be in force for the iteration rather than for the expression that
    starts it. Wrapping the iterator puts it there and leaves the surface's loop body — which is
    long, and is about accumulating the audit row — exactly as it was.

    An async generator does not get a context of its own in CPython, so the value set here is the
    value the surface's loop sees, and it is reset when the stream is exhausted or closed.
    """
    with model_call_span(model, purpose=purpose):
        async for chunk in chunks:
            yield chunk


def instrument_outgoing_calls(engine: AsyncEngine | None = None) -> OutgoingCallsTraced:
    """Trace the calls this process makes: HTTP through httpx, SQL through ``engine``.

    Imported inside the function, like `FastAPIInstrumentor` in `app.py`: the packages are only
    needed when telemetry is on, and an import at module scope would make a gateway with
    `AIRA_OTEL_ENABLED=false` pay for them at start-up.

    ``engine`` is passed rather than left to the library's ``create_engine`` wrapper, and that is
    not belt-and-braces: `db/base.py` imports `create_async_engine` by name at module import, so
    the wrapper the instrumentor installs on the *module attribute* is never the function this
    project calls. Instrumenting the engine that exists is the only form that works here.
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
        # One engine per process — the API builds one in `create_app`, each worker builds one of
        # its own. The instrumentor is a singleton that refuses a second `instrument()` call, so a
        # second engine in one process would need `engines=[...]` at this call rather than a
        # second call; there is no such process today and this returns `False` rather than
        # pretending, so nobody reads a `True` about an engine that is not traced.
        sqlalchemy_instrumentor.instrument(engine=engine.sync_engine if engine else None)

    _log.info("outgoing_calls_instrumented", http=http, database=database)
    return OutgoingCallsTraced(http=http, database=database)
