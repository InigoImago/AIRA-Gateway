"""Writing the request log off the request path (FRD-405 §4.4).

The property being protected is not speed — it is that moving the write off the request path
does not quietly cost an audit row. So most of this is about the awkward moments: a saturated
queue, a failing write, and a shutdown with work still pending.

A note for whoever changes these next: in-memory SQLite shares **one** connection across every
session (StaticPool), so reading the table while the worker is mid-write puts two transactions on
the same connection and the result is undefined. That is a property of the test database, not of
the writer — Postgres hands each session its own connection. The tests below therefore assert on
the queue while work is in flight, and only touch the table once it has drained.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog, UseCaseRead
from aira_gateway.persistence.redaction import NoOpRedactor
from aira_gateway.persistence.writer import PendingLog, RequestLogWriter


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def concurrent_sessionmaker(tmp_path):
    """A database that can take **two writers at once**.

    The module docstring's warning is not advice, it is a constraint: in-memory SQLite behind a
    `StaticPool` hands every session the *same* connection, so a test that deliberately makes the
    worker and an inline write overlap is putting two transactions on one connection, and the
    result is undefined — it fails perhaps one run in five with "cannot operate on a closed
    database", which reads as a defect in the writer and is not one.

    A file gives each session its own connection, like Postgres does, so the overlap the test is
    *about* becomes legal. The alternative — asserting the invariant without the overlap — would
    be testing something else and calling it this.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'writer.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _entry(operation: str = "generateContent", use_case: str | None = None) -> PendingLog:
    return PendingLog(
        subject="alice",
        auth_method="api_key",
        use_case=use_case,
        source_ip="127.0.0.1",
        operation=operation,
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=5,
        trace_id=None,
        request_payload={"contents": [{"parts": [{"text": "personnel number 4711"}]}]},
        response_payload={"text": "ok"},
        cost_nanos=100,
        api="gemini",
    )


async def _rows(sessionmaker) -> list[RequestLog]:
    async with sessionmaker() as session:
        result = await session.execute(select(RequestLog))
        return list(result.scalars())


def _writer(sessionmaker, **kwargs) -> RequestLogWriter:
    settings = kwargs.pop("settings", GatewaySettings())
    return RequestLogWriter(sessionmaker, settings, NoOpRedactor(), **kwargs)


# ---- the queued path -----------------------------------------------------------------------


async def test_a_queued_entry_is_written_by_the_worker(sessionmaker) -> None:
    writer = _writer(sessionmaker, max_queue=8)
    await writer.start()

    await writer.submit(_entry())
    await writer.drain()

    rows = await _rows(sessionmaker)
    assert len(rows) == 1
    assert rows[0].subject == "alice"
    await writer.stop()


async def test_submitting_does_not_wait_for_the_write(sessionmaker) -> None:
    """The whole point: the caller hands the row over and returns while it is still unwritten."""
    writer = _writer(sessionmaker, max_queue=8)
    await writer.start()

    await writer.submit(_entry())

    assert writer.pending == 1  # accepted, not yet persisted
    assert writer.written_inline == 0

    await writer.drain()
    assert writer.pending == 0
    assert len(await _rows(sessionmaker)) == 1
    await writer.stop()


async def test_shutdown_drains_rather_than_discarding(sessionmaker) -> None:
    """A redeploy must not throw away audit rows that were already accepted."""
    writer = _writer(sessionmaker, max_queue=64)
    await writer.start()
    for index in range(20):
        await writer.submit(_entry(operation=f"op-{index}"))

    await writer.stop()

    assert len(await _rows(sessionmaker)) == 20


# ---- the awkward moments -------------------------------------------------------------------


async def test_a_full_queue_writes_inline_instead_of_dropping(concurrent_sessionmaker) -> None:
    """Losing rows under load would lose exactly the ones from the incident somebody later has
    to reconstruct. Backpressure on the caller producing the load is the better trade.

    On the file-backed database, because this is the one test whose whole subject is an inline
    write happening *while* the worker writes — see `concurrent_sessionmaker`.
    """
    writer = _writer(concurrent_sessionmaker, max_queue=1)
    await writer.start()

    for index in range(20):
        await writer.submit(_entry(operation=f"op-{index}"))

    # Not an exact count: an inline write yields, which lets the worker free a slot, so how many
    # take the fallback depends on scheduling. The invariant is that the fallback engaged at all
    # and that nothing was lost to it.
    assert writer.written_inline > 0

    await writer.stop()
    assert len(await _rows(concurrent_sessionmaker)) == 20


async def test_a_row_submitted_while_stopping_is_not_lost(sessionmaker) -> None:
    """`stop()` drains, cancels the worker, then awaits it — and that await is a real yield
    point. A request landing in that window used to queue against a worker that would never
    consume it again, so the row was silently dropped by the very shutdown that promises not to
    discard anything."""
    writer = _writer(sessionmaker, max_queue=8)
    await writer.start()

    async def submit_during_shutdown() -> None:
        await asyncio.sleep(0)  # let stop() reach its await
        await writer.submit(_entry(operation="late"))

    await asyncio.gather(writer.stop(), submit_during_shutdown())

    operations = [row.operation for row in await _rows(sessionmaker)]
    assert "late" in operations


async def test_submitting_after_a_full_stop_still_writes(sessionmaker) -> None:
    """Once stopped there is no worker, so the entry has to be written inline rather than
    queued into nothing."""
    writer = _writer(sessionmaker, max_queue=8)
    await writer.start()
    await writer.stop()

    await writer.submit(_entry(operation="after-stop"))

    assert [row.operation for row in await _rows(sessionmaker)] == ["after-stop"]


class _RedactorThatFailsOnce:
    """Fails the first payload it is handed, then behaves.

    The failure has to be produced deliberately rather than by feeding the database something
    invalid: SQLite does not enforce ``VARCHAR`` lengths, so an over-long value — the obvious
    way to write this test — is stored happily and the test proves nothing at all.
    """

    def __init__(self) -> None:
        self.calls = 0

    def redact(self, payload: dict) -> dict:  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("redaction blew up")
        return payload


async def test_a_failing_write_does_not_kill_the_worker(sessionmaker) -> None:
    """Otherwise one bad row would silently stop every subsequent one from being written."""
    writer = RequestLogWriter(
        sessionmaker, GatewaySettings(), _RedactorThatFailsOnce(), max_queue=8
    )
    await writer.start()

    await writer.submit(_entry(operation="doomed"))
    await writer.submit(_entry(operation="fine"))
    await writer.drain()

    operations = [row.operation for row in await _rows(sessionmaker)]
    assert operations == ["fine"], "the bad row must be dropped, and only it"
    await writer.stop()


async def test_without_a_worker_the_write_happens_inline(sessionmaker) -> None:
    """A queue size of zero is a supported configuration, not only a test convenience."""
    writer = _writer(sessionmaker, max_queue=0)
    await writer.start()

    await writer.submit(_entry())

    assert len(await _rows(sessionmaker)) == 1
    await writer.stop()  # a no-op, and must not raise


async def test_stopping_a_writer_that_never_started_is_not_an_error(sessionmaker) -> None:
    await _writer(sessionmaker).stop()


# ---- the storage decision travels with the entry --------------------------------------------


async def test_the_use_cases_storage_setting_is_applied_by_the_worker(sessionmaker) -> None:
    """The decision needs a database read, which is precisely what must not happen on the
    request path — so it is made where the row is written."""
    async with sessionmaker() as session:
        session.add(UseCaseRead(slug="private-uc", name="private", store_payloads=False))
        await session.commit()

    writer = _writer(sessionmaker, max_queue=8)
    await writer.start()
    await writer.submit(_entry(use_case="private-uc"))
    await writer.drain()

    row = (await _rows(sessionmaker))[0]
    assert row.request_payload is None
    assert row.total_tokens is None or row.status == 200  # the accounting still landed
    await writer.stop()


async def test_the_installation_kill_switch_still_wins(sessionmaker) -> None:
    async with sessionmaker() as session:
        session.add(UseCaseRead(slug="open-uc", name="open", store_payloads=True))
        await session.commit()

    writer = _writer(sessionmaker, settings=GatewaySettings(store_payloads=False), max_queue=8)
    await writer.start()
    await writer.submit(_entry(use_case="open-uc"))
    await writer.drain()

    assert (await _rows(sessionmaker))[0].request_payload is None
    await writer.stop()


async def test_concurrent_submissions_all_land(sessionmaker) -> None:
    writer = _writer(sessionmaker, max_queue=128)
    await writer.start()

    await asyncio.gather(*(writer.submit(_entry(operation=f"op-{i}")) for i in range(50)))
    await writer.drain()

    assert len(await _rows(sessionmaker)) == 50
    await writer.stop()


class _GatedSessionmaker:
    """A sessionmaker whose **first** write waits for a gate before it finishes.

    The window this file's shutdown tests are about — `_stopping` is set, the worker still exists —
    is a few microseconds wide in real time, and `test_a_row_submitted_while_stopping_is_not_lost`
    tried to land in it with `asyncio.sleep(0)`. That test is green and the mutation harness kills
    nothing with it: removing the `_stopping` check from `submit` leaves it passing, because the
    timing lands after the worker is already gone and the entry is written inline for the *other*
    reason. Green for a reason unrelated to its name.

    So the window is made wide and deterministic instead: the worker blocks inside its first write
    until this gate opens, which is exactly as long as `stop()` waits on the drain.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.gate = asyncio.Event()
        self.first = True

    def __call__(self):
        wait = self.first
        self.first = False
        return _GatedSession(self._inner(), self.gate if wait else None)


class _GatedSession:
    def __init__(self, inner, gate) -> None:
        self._inner = inner
        self._gate = gate

    async def __aenter__(self):
        session = await self._inner.__aenter__()
        if self._gate is not None:
            await self._gate.wait()
        return session

    async def __aexit__(self, *exc) -> None:
        await self._inner.__aexit__(*exc)


async def test_a_row_submitted_while_the_worker_still_exists_is_written_at_once(
    concurrent_sessionmaker,
) -> None:
    """The property `M18` claims, tested where it can actually fail.

    `stop()` sets `_stopping` and then waits; the worker is cancelled at the end. An entry handed
    over inside that window must be written **here and now**, not queued — a queue whose consumer
    is about to be cancelled is a hole with a shutdown's promise written on it.

    The discriminator is *when*: the late row is asserted present **while the worker is still
    blocked on its own write**. Queue it instead and it cannot possibly be there yet, which is what
    makes this fail when the `or self._stopping` is removed and what the previous version of this
    test could not see.

    On the file-backed database, because an inline write and a blocked worker write overlap here by
    construction — see `concurrent_sessionmaker`.
    """
    gated = _GatedSessionmaker(concurrent_sessionmaker)
    writer = _writer(gated, max_queue=8)
    await writer.start()
    await writer.submit(_entry(operation="blocking"))

    stopping = asyncio.create_task(writer.stop())
    for _ in range(5):
        await asyncio.sleep(0)  # let the worker reach the gate and stop() reach its drain

    await writer.submit(_entry(operation="late"))

    written = [row.operation for row in await _rows(concurrent_sessionmaker)]
    assert "late" in written, (
        "the row handed over during shutdown was queued against a worker on its way out; "
        f"the table holds {written}"
    )

    gated.gate.set()
    await stopping
    assert sorted(row.operation for row in await _rows(concurrent_sessionmaker)) == [
        "blocking",
        "late",
    ]


def test_a_value_the_column_cannot_take_costs_the_value_and_not_the_row() -> None:
    """`json` columns are stricter than Python's parser, and one bad value used to cost the row.

    `Infinity`, `-Infinity` and `NaN` are not JSON — RFC 8259 has no such literals — and Postgres
    refuses an insert carrying one. Measured on 2026-08-19: a request with `maxTokens: 1e309` was
    correctly refused with a `422`, the audit write then failed with *"Token \\"Infinity\\" is
    invalid"*, and **no row was written**. A caller could choose not to be recorded.

    The boundary refuses such a body now, which closes the caller's door. This is the upstream's:
    a response payload is a model's output and nobody here picked its contents. Losing the value
    is a smaller failure than losing the row — and naming it is smaller still.
    """
    from aira_gateway.persistence.writer import storable

    payload = {
        "score": float("inf"),
        "ratio": float("nan"),
        "nested": [{"deep": float("-inf")}, "fine"],
        "text": "ordinary",
    }

    cleaned = storable(payload)

    assert cleaned["text"] == "ordinary"
    assert cleaned["nested"][1] == "fine"
    for rendered in (cleaned["score"], cleaned["ratio"], cleaned["nested"][0]["deep"]):
        assert isinstance(rendered, str) and "unrepresentable" in rendered
    # The whole point: what comes out is something a `json` column will take.
    json.dumps(cleaned, allow_nan=False)


def test_a_lone_surrogate_in_a_payload_does_not_cost_the_row_either() -> None:
    """Same shape, different door: a model that echoes one back would erase the record of its own
    answer, and the payload is not something anybody here chose."""
    from aira_gateway.persistence.writer import storable

    cleaned = storable({"text": "hi\ud800there", "ok": "plain"})

    assert cleaned["ok"] == "plain"
    assert "unrepresentable" in cleaned["text"]
    json.dumps(cleaned, allow_nan=False).encode("utf-8")
