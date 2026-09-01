"""The model call and the database read are spans of their own (`FRD-117` FR-5).

`FRD-117` §5.3 specified `HTTPXClientInstrumentor` and `SQLAlchemyInstrumentor`, §10a recorded
*"FR-1 through FR-6"* as built, and `GAP-ANALYSIS.md` row 21 repeated it. Neither package was a
declared dependency and neither instrumentor was ever called: a trace showed the gateway's server
span with **nothing underneath it**, so the first question anybody asks of a gateway — *is this
slow because of us or because of the model* — could not be answered from the trace.

So these tests ask the **spans**, never the wiring. A test that asserted
`instrument_outgoing_calls` had been called would pass against a hook the library silently drops,
which is exactly the failure mode this feature has (`async_request_hook` is ignored unless it is a
coroutine function, and every upstream adapter here uses `AsyncClient`).

The upstream call is made against a **closed loopback port**: the span is created before the
transport runs, so a refused connection produces the same client span as a served request without
this layer needing a server. `LESSONS.md` §7 — the test has to make the data it measures.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text

import aira_gateway.app as app_module
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

#: Shaped like the credential `upstreams/gemini.py` puts in `?key=` on every Google AI Studio
#: call. A literal rather than a fixture, so a failure prints the thing that leaked.
UPSTREAM_KEY = "AIzaSy-this-would-be-the-installations-key"

#: Nothing listens here, and the connection is refused immediately. The span is opened before the
#: transport is asked to do anything, so a refusal exercises exactly the path a served call takes
#: without this layer needing a server or a network.
CLOSED_PORT = "http://127.0.0.1:1"


@pytest.fixture
def traced(instrumentation_restored: None) -> Iterator[tuple[FastAPI, InMemorySpanExporter]]:
    """A gateway with telemetry on, exporting into memory instead of to a collector.

    `configure_observability` installs **global** providers with real OTLP exporters, and
    `conftest.py` records at length what leaving one of those behind costs the rest of the
    session. What is under test here is the instrumentation, so the provider is a local one and
    both instrumentors are removed again on the way out.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    original_configure = app_module.configure_observability
    app_module.configure_observability = lambda **_kwargs: True  # type: ignore[assignment]
    previous = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    try:
        app = create_app(
            GatewaySettings(
                environment="local",
                otel_enabled=True,
                otel_endpoint="http://127.0.0.1:4318",
            )
        )
        yield app, exporter
    finally:
        # The instrumentors themselves are `instrumentation_restored`'s, which also clears them on
        # the way **in** — a test earlier in the session that leaves them on would otherwise make
        # `create_app` here a no-op and this exporter empty.
        trace._TRACER_PROVIDER = previous
        app_module.configure_observability = original_configure  # type: ignore[assignment]


def _client_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return [span for span in exporter.get_finished_spans() if span.kind is trace.SpanKind.CLIENT]


async def _call_a_closed_upstream(query: dict[str, str] | None = None) -> None:
    """One outbound request through a plain `httpx.AsyncClient`, as every adapter here makes."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        with pytest.raises(httpx.HTTPError):
            await client.post(
                f"{CLOSED_PORT}/v1beta/models/gemini-2.5-flash:generateContent",
                params=query or {},
                json={},
            )


# ═══ the call that matters ═══════════════════════════════════════════════════════════════════════


async def test_an_upstream_call_appears_as_a_client_span(
    traced: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    """Without this the trace of a nine-second request is one span, nine seconds long."""
    _app, exporter = traced
    await _call_a_closed_upstream()

    spans = _client_spans(exporter)
    assert spans, (
        "an outgoing HTTP call produced no client span; the gateway's own trace then says nothing "
        "about how long the model took or whether it answered (FRD-117 FR-5)"
    )
    assert spans[0].name == "POST"


async def test_the_upstream_credential_never_reaches_a_span(
    traced: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    """`?key=` is how the Gemini dialect authenticates — *ours*, outbound, on every call.

    The library records the request URL verbatim (its own `redact_url` removes `user:password@`
    and nothing else), so instrumenting httpx without a hook writes this installation's upstream
    key into a span attribute on every model call, and ships it to a trace backend with a
    different set of readers from the secret store it came out of.

    The assertion is over **every** attribute rather than over `http.url`, because which name
    carries the URL depends on a semantic-convention opt-in that a library upgrade can flip — and
    a redaction that names one attribute is one that goes quiet on the day it is renamed.
    """
    _app, exporter = traced
    await _call_a_closed_upstream({"key": UPSTREAM_KEY, "alt": "sse"})

    spans = _client_spans(exporter)
    assert spans, "no client span to check the redaction on"
    leaked = [
        f"{key}={value}"
        for span in spans
        for key, value in (span.attributes or {}).items()
        if isinstance(value, str) and UPSTREAM_KEY in value
    ]
    assert not leaked, f"the upstream credential reached a span attribute: {leaked}"
    assert any(
        "REDACTED" in value
        for span in spans
        for value in (span.attributes or {}).values()
        if isinstance(value, str)
    ), "nothing was redacted, so the URL was not recorded at all — check the attribute names"


async def test_the_rest_of_the_query_survives_the_redaction(
    traced: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    """A redaction that took the whole URL would make the span useless for the thing it is for."""
    _app, exporter = traced
    await _call_a_closed_upstream({"key": UPSTREAM_KEY, "alt": "sse"})

    urls = [
        value
        for span in _client_spans(exporter)
        for value in (span.attributes or {}).values()
        if isinstance(value, str) and "generateContent" in value
    ]
    assert urls, "the request URL is not on the span at all"
    assert "alt=sse" in urls[0]


# ═══ the read that decides whether the call may happen ════════════════════════════════════════════


async def test_a_database_read_appears_as_a_span_carrying_no_bound_value(
    traced: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    """`FRD-117` §5.3's reason, checked rather than asserted in prose.

    The requirement was written as *"statement text hidden"* because *"a bound parameter can carry
    a prompt fragment or a subject identifier"*. What the instrumentation records is the statement
    with its **placeholders** and never the values — SQLAlchemy hands the two to the event hook
    separately. That is the property worth keeping, so it is the one asserted; the FRD now says
    what is recorded instead of what is hidden.
    """
    app, exporter = traced
    secret = "a-prompt-fragment-nobody-should-find-in-a-trace"
    async with app.state.db_sessionmaker() as session:
        await session.execute(text("SELECT :value"), {"value": secret})

    statements = [
        value
        for span in _client_spans(exporter)
        for key, value in (span.attributes or {}).items()
        if key in ("db.statement", "db.query.text")
    ]
    assert statements, "a database query produced no span (FRD-117 FR-5)"
    assert not any(secret in str(statement) for statement in statements), (
        f"a bound value reached a span attribute: {statements}"
    )
