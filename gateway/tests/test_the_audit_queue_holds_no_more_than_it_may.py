"""The audit queue is bounded by the bodies it holds, not only by the rows.

The queue exists so that persistence does not hold up the request path, and it is bounded because
an unbounded one would only move the exhaustion from the connection pool to memory. It was bounded
in rows: up to `AIRA_LOG_QUEUE_SIZE` entries, each keeping its request body (up to 8 MiB) and its
answer until the worker reached it — also where nothing would be stored, since the switch was read
only when the row was written. While the database was slow that is gigabytes, and a process killed
for its memory loses every row still queued.

So a body the installation will not store is not kept while its row waits, and the queue admits a
row only while the bodies it holds stay under a bound; past it, the row is written inline — the
same backpressure a full queue applies. A row without a body is never turned away by that bound.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import tracemalloc
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog
from aira_gateway.persistence.redaction import NoOpRedactor
from aira_gateway.persistence.writer import PendingLog, RequestLogWriter

BODY = 256 * 1024


@pytest.fixture
async def sessions(tmp_path: Any) -> Any:
    """A file, so an inline write and the worker's can be open at once, as on Postgres."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _entry(index: int, *, body: int = BODY) -> PendingLog:
    """A row as a request leaves it: its body, and the bytes the caller sent for it."""
    text = f"{index:08d}" + "x" * body if body else ""
    return PendingLog(
        subject="alice",
        auth_method="api_key",
        use_case=None,
        source_ip="127.0.0.1",
        operation=f"op-{index}",
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=5,
        trace_id=None,
        request_payload={"contents": [{"parts": [{"text": text}]}]} if body else None,
        response_payload={"text": "ok"},
        cost_nanos=100,
        api="gemini",
        request_bytes=body + 64 if body else None,
    )


class _Gate:
    """Holds the first write inside its session until opened — the database not answering yet —
    while every later session, an inline write's, goes through. A test lets the worker reach it
    (``entered``) before it submits more, so the write held is the worker's."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.open = asyncio.Event()
        self.entered = asyncio.Event()
        self._first = True

    def __call__(self) -> Any:
        wait, self._first = self._first, False
        return _Held(self._inner(), self if wait else None)


class _Held:
    def __init__(self, inner: Any, gate: _Gate | None) -> None:
        self._inner, self._gate = inner, gate

    async def __aenter__(self) -> Any:
        session = await self._inner.__aenter__()
        if self._gate is not None:
            self._gate.entered.set()
            await self._gate.open.wait()
        return session

    async def __aexit__(self, *exc: Any) -> None:
        await self._inner.__aexit__(*exc)


async def _count(sessions: Any) -> int:
    async with sessions() as session:
        return int((await session.execute(select(func.count()).select_from(RequestLog))).scalar())


async def test_a_body_that_will_not_be_stored_is_not_held_while_its_row_waits(
    sessions: Any,
) -> None:
    gate = _Gate(sessions)
    writer = RequestLogWriter(
        gate, GatewaySettings(store_payloads=False), NoOpRedactor(), max_queue=512
    )
    await writer.start()

    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for index in range(64):
        await writer.submit(_entry(index))  # the request has answered and let go of its body
    gc.collect()
    held = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()

    gate.open.set()
    await writer.stop()
    assert held < 4 * 2**20, f"{held / 2**20:.0f} MiB of bodies held that will never be written"
    assert await _count(sessions) == 64


async def test_the_queue_holds_no_more_body_than_its_bound(sessions: Any) -> None:
    gate = _Gate(sessions)
    bound = 4 * BODY
    writer = RequestLogWriter(
        gate, GatewaySettings(), NoOpRedactor(), max_queue=512, max_queue_bytes=bound
    )
    await writer.start()

    await writer.submit(_entry(0))
    await gate.entered.wait()  # the worker is writing it, and the body still counts
    for index in range(1, 10):
        await writer.submit(_entry(index))
    admitted = 10 - writer.written_inline
    assert writer.written_inline > 0, "every body was queued, past the bound"
    assert admitted * BODY <= bound

    gate.open.set()
    await writer.drain()
    # Written, the rows give their room back: the next body is queued, not written inline.
    inline = writer.written_inline
    await writer.submit(_entry(10))
    assert writer.written_inline == inline
    await writer.stop()
    assert await _count(sessions) == 11


async def test_a_row_without_a_body_is_never_turned_away_by_that_bound(sessions: Any) -> None:
    """Most rows carry none — a refusal, a pipeline step's call — and writing those inline would
    put the request path behind a slow database for no memory saved."""
    gate = _Gate(sessions)
    writer = RequestLogWriter(
        gate, GatewaySettings(), NoOpRedactor(), max_queue=512, max_queue_bytes=2 * BODY
    )
    await writer.start()

    await writer.submit(_entry(0))  # most of the bound, held by the worker that is held too
    await gate.entered.wait()
    await writer.submit(_entry(1))  # past it: written inline
    assert writer.written_inline == 1
    for index in range(2, 7):
        await writer.submit(dataclasses.replace(_entry(index, body=0), response_payload=None))

    assert writer.written_inline == 1
    gate.open.set()
    await writer.stop()
    assert await _count(sessions) == 7
