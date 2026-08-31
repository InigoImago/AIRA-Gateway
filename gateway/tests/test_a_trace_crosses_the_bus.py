"""A change made in the console and the gateway applying it are **one trace** (`FRD-615`).

**Written at the wire, not at the ends.** Both halves of this existed and were tested: the producer
has attached a `traceparent` to every message since `FRD-001`, `context_from_kafka_headers` has had
a round-trip test for as long, and the trace still stopped dead at the bus — because the producer
injected an *empty* context (an outbox publishes from a process with no span) and the consumer read
nothing at all. Two correct halves and no wire, and `LESSONS.md` §1 says what follows from that:
**a test that builds the object under test is a test of the reader**, so these start upstream and
drive the real path.

The case that would have caught it is `test_the_context_survives_the_outbox`: it never constructs a
`traceparent`, it produces one from a span the way a request does, carries it the way a row does,
and asks what the consumer ends up in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.kafka import KafkaRecord, kafka_headers_for
from aira_common.observability import traceparent_from_context
from aira_gateway.consumer.worker import apply_one_message
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


class _Message:
    """What aiokafka hands the worker, in the fields it reads."""

    def __init__(self, headers: list[tuple[str, bytes]], value: dict[str, Any]) -> None:
        self.headers = headers
        self.value = value
        self.topic = "aira.usecases"
        self.partition = 0
        self.offset = 17


@pytest.fixture
def recorded() -> Any:
    """A real tracer provider whose spans land in memory.

    Not the global one: `conftest.py` exists partly because two tests once installed global
    providers and left every later test doing background network I/O. This one is local to the
    test and thrown away with it.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _headers(event_type: str, traceparent: str = "") -> list[tuple[str, bytes]]:
    return [("event_type", event_type.encode()), *kafka_headers_for(traceparent)]


# ═══ the wire ═══════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_context_survives_the_outbox(
    sessions: async_sessionmaker[AsyncSession], recorded: Any
) -> None:
    """The whole path: a request's span → a stored string → a message → the consumer's span.

    The stored step is the one that was missing. A publisher has no span of its own, so reading the
    *ambient* context there — which is what the producer did — yields nothing, and the message goes
    out untraced however carefully both ends were written.
    """
    provider, exporter = recorded
    tracer = provider.get_tracer("test")

    # 1. the console's request, where a span exists
    with tracer.start_as_current_span("PATCH /api/v1/use-cases/uc-a") as request_span:
        stored = traceparent_from_context()
        expected = request_span.get_span_context().trace_id
    assert stored, "a request with a span must yield a context to store"

    # 2. the relay, in another process, with no span at all
    record = KafkaRecord(
        topic="aira.usecases",
        key="uc-a",
        event_type="usecase.upserted",
        payload={"slug": "uc-a"},
        traceparent=stored,
    )
    headers = [("event_type", record.event_type.encode()), *kafka_headers_for(record.traceparent)]

    # 3. the gateway consumer
    with _using(provider):
        applied = await apply_one_message(sessions, _Message(headers, record.payload))

    assert applied == "usecase.upserted"
    spans = exporter.get_finished_spans()
    consumer_spans = [span for span in spans if span.name.endswith("process")]
    assert consumer_spans, "the consumer opened no span"
    assert consumer_spans[0].context.trace_id == expected, (
        "the gateway's work is in a different trace from the request that caused it"
    )


@pytest.mark.asyncio
async def test_a_message_with_no_context_still_applies(
    sessions: async_sessionmaker[AsyncSession], recorded: Any
) -> None:
    """An event published by the seed or a management command has no request behind it. It gets a
    trace of its own rather than being dropped — the honest answer is *no caller*, not *no work*."""
    provider, exporter = recorded
    with _using(provider):
        applied = await apply_one_message(
            sessions, _Message(_headers("usecase.upserted"), {"slug": "uc-b"})
        )

    assert applied == "usecase.upserted"
    assert [s for s in exporter.get_finished_spans() if s.name.endswith("process")]


@pytest.mark.asyncio
async def test_an_event_that_cannot_be_applied_is_red_in_the_trace(
    sessions: async_sessionmaker[AsyncSession], recorded: Any
) -> None:
    """The consumer swallows the exception on purpose — one bad event must not take it down — and
    that is exactly what stops the failure reaching the span on its own. A trace that stays green
    while the log says otherwise is worse than no trace, because somebody reads the green one."""
    provider, exporter = recorded
    with _using(provider):
        applied = await apply_one_message(
            sessions,
            # `usecase.upserted` indexes `payload["slug"]`; an empty payload is the shape a newer
            # Management renaming a field would produce.
            _Message(_headers("usecase.upserted"), {}),
        )

    assert applied is None, "a failed event must not be reported as applied"
    span = next(s for s in exporter.get_finished_spans() if s.name.endswith("process"))
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.events, "the exception is recorded on the span, not only in the log"


@pytest.mark.asyncio
async def test_the_span_says_which_message_it_was(
    sessions: async_sessionmaker[AsyncSession], recorded: Any
) -> None:
    """Topic, partition and offset — what somebody needs to go and find the message itself. The
    log line already carries them; a trace that omits them sends the reader back to the log."""
    provider, exporter = recorded
    with _using(provider):
        await apply_one_message(sessions, _Message(_headers("usecase.upserted"), {"slug": "uc-c"}))

    span = next(s for s in exporter.get_finished_spans() if s.name.endswith("process"))
    assert span.name == "aira.usecases process"
    assert span.attributes["messaging.system"] == "kafka"
    assert span.attributes["messaging.destination.name"] == "aira.usecases"
    assert span.attributes["aira.event_type"] == "usecase.upserted"
    assert span.attributes["messaging.kafka.offset"] == 17


@pytest.mark.asyncio
async def test_a_message_with_no_type_opens_no_span(
    sessions: async_sessionmaker[AsyncSession], recorded: Any
) -> None:
    """It is refused before any work is attempted, and a span for a message that was never applied
    would put a green row in the trace view for something that did not happen."""
    provider, exporter = recorded
    with _using(provider):
        assert await apply_one_message(sessions, _Message([], {"slug": "uc-d"})) is None
    assert not [s for s in exporter.get_finished_spans() if s.name.endswith("process")]


# ═══ the producer's half ════════════════════════════════════════════════════════════════════════


def test_the_stored_context_wins_over_the_ambient_one(recorded: Any) -> None:
    """A relay that happened to have a span of its own must still publish under the **request's**
    trace. Otherwise the message joins the publisher's trace, which is a fact about our
    infrastructure rather than about the change somebody made."""
    provider, _ = recorded
    tracer = provider.get_tracer("test")
    stored = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    with tracer.start_as_current_span("relay-loop"):
        headers = dict(kafka_headers_for(stored))
    assert headers["traceparent"].decode() == stored


def test_with_nothing_stored_the_current_span_is_used(recorded: Any) -> None:
    """The direct path — anything that publishes inside a request rather than through the outbox —
    keeps working, which is why this is one function and not a branch at the call site."""
    provider, _ = recorded
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("direct") as span:
        headers = dict(kafka_headers_for())
        expected = trace.format_trace_id(span.get_span_context().trace_id)
    assert expected in headers["traceparent"].decode()


def test_with_neither_the_message_simply_carries_no_context() -> None:
    assert kafka_headers_for() == []


# ═══ helpers ════════════════════════════════════════════════════════════════════════════════════


class _using:
    """Make ``provider`` the tracer provider for the duration of the block.

    OpenTelemetry refuses to replace a global provider once set, so this swaps the private slot
    and puts back what was there. Confined to this file: a test that leaks a provider makes every
    later test do background network I/O, which `conftest.py` was written about.
    """

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._previous: Any = None

    def __enter__(self) -> None:
        self._previous = trace._TRACER_PROVIDER
        trace._TRACER_PROVIDER = self._provider

    def __exit__(self, *exc: object) -> None:
        trace._TRACER_PROVIDER = self._previous
