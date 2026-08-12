"""Shutdown writes what is queued, even when the worker is not there to write it.

`RequestLogWriter.stop()` promises that *"a redeploy must not discard pending audit rows"*, and it
kept that promise by waiting on `_queue.join()` — which returns only when every entry has been
marked done, and **only the worker marks them**. If the worker is gone, that is a wait for a signal
nobody will send: shutdown hangs until the orchestrator sends `SIGKILL`, and the entire queue is
lost by the call whose whole purpose is not to lose it. The failure mode is the exact inverse of
the guarantee, which is the kind worth a test.

`_run` catches `Exception`, so reaching this needs something it does not catch — an outside
cancellation of the worker task, or a `BaseException` from the write path. Rare; and the cost of
being wrong is every pending row from a shutdown that was already going badly, which is exactly
when an audit trail is read.

Both cases below would have **hung** before the fix rather than failed, so they carry a timeout:
a test that hangs reports nothing at all, and the suite would have looked stuck rather than red.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.persistence.writer import PendingLog, RequestLogWriter


def _entry(operation: str = "generateContent") -> PendingLog:
    return PendingLog(
        subject="ada",
        auth_method="api_key",
        use_case="uc",
        source_ip=None,
        operation=operation,
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=1,
        trace_id=None,
        request_payload=None,
        response_payload=None,
        cost_nanos=None,
    )


class _Recorder(RequestLogWriter):
    """A writer whose rows go to a list instead of a database.

    Overriding `_write` rather than substituting a session: what is under test is the *shutdown
    sequence*, and a stand-in for the database would leave the queue mechanics untested while
    looking as though they were.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(None, GatewaySettings(), None, **kwargs)  # type: ignore[arg-type]
        self.written: list[str] = []

    async def _write(self, entry: PendingLog) -> None:
        self.written.append(entry.operation)


async def test_a_normal_shutdown_drains_the_queue() -> None:
    """The property that already held, kept so a regression shows up as two failures rather than
    one — and so the case below is read as an addition rather than a replacement."""
    writer = _Recorder(max_queue=8)
    await writer.start()
    for index in range(4):
        await writer.submit(_entry(f"op-{index}"))

    async with asyncio.timeout(5):
        await writer.stop()

    assert sorted(writer.written) == ["op-0", "op-1", "op-2", "op-3"]


async def test_a_dead_worker_does_not_hang_the_shutdown() -> None:
    """The worker is cancelled from outside — something `_run` cannot catch — with rows still
    queued. Before the fix this waited on a `join()` nobody would ever complete."""
    writer = _Recorder(max_queue=8)
    await writer.start()
    # Queued while the worker is stopped, so nothing can be consumed before `stop()` runs.
    worker = writer._worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    for index in range(3):
        writer._queue.put_nowait(_entry(f"orphan-{index}"))

    async with asyncio.timeout(5):
        await writer.stop()

    # Written, not discarded: the rows from a shutdown that went badly are the ones somebody later
    # has to reconstruct an incident from.
    assert sorted(writer.written) == ["orphan-0", "orphan-1", "orphan-2"]


async def test_a_failing_write_during_shutdown_does_not_stop_the_rest() -> None:
    """One bad row must not take the others with it — the same reasoning the worker's own loop
    already applies, and it has to hold on this path too or a single failure at shutdown discards
    everything behind it."""

    class _OneBadRow(_Recorder):
        async def _write(self, entry: PendingLog) -> None:
            if entry.operation == "bad":
                raise RuntimeError("the database went away mid-shutdown")
            self.written.append(entry.operation)

    writer = _OneBadRow(max_queue=8)
    await writer.start()
    worker = writer._worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    for operation in ("good-1", "bad", "good-2"):
        writer._queue.put_nowait(_entry(operation))

    async with asyncio.timeout(5):
        await writer.stop()

    assert sorted(writer.written) == ["good-1", "good-2"]
