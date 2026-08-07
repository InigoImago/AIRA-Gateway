"""Writing the request log off the request path (FRD-405 §4.4).

``record_request`` used to be awaited before the response was returned, so every caller waited
for its own audit row to be committed. ``CLAUDE.md`` requires the opposite — *persistence and
event emission must not block the gateway request path* — and the code had been contradicting it.

The queue is **bounded**. An unbounded one would only move the exhaustion it is meant to prevent
from the connection pool to memory, which is the same failure with a slower onset.

A full queue writes **inline** rather than dropping the entry. That applies backpressure to the
caller producing the load, which is the right place for it, and it keeps a property that matters
more than latency: the request log never silently loses rows. The rows lost under pressure would
be exactly the ones from the incident somebody later has to reconstruct.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.attachments import strip_attachments
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import UseCaseRead
from aira_gateway.persistence.redaction import Redactor
from aira_gateway.persistence.service import RequestLogService

_log = get_logger("aira_gateway.persistence")


@dataclass(frozen=True, slots=True)
class PendingLog:
    """One audit row, captured on the request path and written afterwards.

    Everything that can only be read from the live request — attribution, the source IP, the
    trace id — is resolved before this is handed over; the worker never touches the request.
    """

    subject: str
    auth_method: str
    use_case: str | None
    source_ip: str | None
    operation: str
    model: str
    status: int
    usage: CanonicalUsage | None
    latency_ms: int | None
    trace_id: str | None
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    cost_nanos: int | None
    # FRD-122. Defaulted so a caller that only knows the old facts still produces a valid row —
    # which matters because a *refusal* often knows nothing else.
    credential: str | None = None
    outcome: str | None = None
    requested_model: str | None = None
    model_selection: str | None = None
    pipeline_decisions: list[dict[str, Any]] | None = None
    degraded: dict[str, str] | None = None
    provider: str | None = None
    publisher: str | None = None
    region: str | None = None
    api: str = "gemini"
    #: Bytes the caller sent, as counted by the body-size middleware (`FRD-501`). NULL where the
    #: count is unknown, never 0 — an unknown size must not be able to look like a small one.
    request_bytes: int | None = None


class RequestLogWriter:
    """Buffers audit rows and writes them from a background worker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Any,
        redactor: Redactor,
        *,
        max_queue: int = 512,
        on_written: Callable[[str | None], None] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._redactor = redactor
        # The fan-out point of `ADR-0014`: the detector learns which scopes saw traffic from the
        # row that was going to be written anyway. A callback rather than a second queue, because
        # the whole cost has to stay "mark three strings".
        self._on_written = on_written
        self._max_queue = max_queue
        self._queue: asyncio.Queue[PendingLog] = asyncio.Queue(maxsize=max(1, max_queue))
        self._worker: asyncio.Task[None] | None = None
        # Set the moment a shutdown begins, not when it finishes. `stop()` awaits the worker,
        # and that await is a real yield point: a request landing in it would otherwise queue
        # against a worker already being cancelled, and the row would be dropped by the very
        # shutdown that promises not to discard anything.
        self._stopping = False
        self.written_inline = 0

    async def start(self) -> None:
        """Start the worker. A queue size of zero keeps writing on the request path.

        That is a supported configuration, not only a test convenience: an operator who needs a
        request to be durably logged *before* its response is returned can ask for it, at the
        cost of the latency this feature exists to remove.
        """
        if self._max_queue <= 0:
            return
        self._stopping = False
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def drain(self) -> None:
        """Wait until everything queued has been written."""
        if self._worker is not None:
            await self._queue.join()

    @property
    def pending(self) -> int:
        """Rows accepted but not yet written. A steadily rising figure means the database is
        slower than the traffic, and the inline fallback is about to start applying backpressure."""
        return self._queue.qsize()

    async def stop(self) -> None:
        """Drain what is queued, then stop. A redeploy must not discard pending audit rows."""
        if self._worker is None:
            return
        self._stopping = True
        await self._queue.join()
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    async def submit(self, entry: PendingLog) -> None:
        """Hand an entry over; falls back to writing it here if the queue is saturated."""
        if self._worker is None or self._stopping:
            # No worker, or one on its way out: write it here rather than queue it into nothing.
            await self._write(entry)
            return
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.written_inline += 1
            _log.warning("request_log_queue_full", operation=entry.operation)
            await self._write(entry)

    async def _run(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self._write(entry)
            except Exception as exc:  # a failed write must never take the worker down with it
                _log.error(
                    "request_log_write_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    operation=entry.operation,
                )
            finally:
                self._queue.task_done()

    async def _write(self, entry: PendingLog) -> None:
        async with self._sessionmaker() as session:
            store = await self._may_store_payloads(session, entry.use_case)

            def _maybe(payload: dict[str, Any] | None) -> dict[str, Any] | None:
                if not store or payload is None:
                    return None
                # Strip first, then redact. A base64 PDF in a JSONB column would make each row
                # megabytes, put binary the gateway never inspected inside the retention boundary,
                # and hand redaction something it cannot process (FRD-110 §5.4). Unconditional,
                # because a deployment that swaps the redactor must not be able to turn it off.
                stripped: dict[str, Any] = strip_attachments(payload)
                return self._redactor.redact(stripped)

            await RequestLogService(session).record(
                subject=entry.subject,
                auth_method=entry.auth_method,
                use_case=entry.use_case,
                source_ip=entry.source_ip,
                operation=entry.operation,
                model=entry.model,
                status=entry.status,
                usage=entry.usage,
                latency_ms=entry.latency_ms,
                trace_id=entry.trace_id,
                request_payload=_maybe(entry.request_payload),
                response_payload=_maybe(entry.response_payload),
                cost_nanos=entry.cost_nanos,
                credential=entry.credential,
                outcome=entry.outcome,
                requested_model=entry.requested_model,
                model_selection=entry.model_selection,
                pipeline_decisions=entry.pipeline_decisions,
                degraded=entry.degraded,
                provider=entry.provider,
                publisher=entry.publisher,
                region=entry.region,
                api=entry.api,
                request_bytes=entry.request_bytes,
            )
        # After the row exists, never before: a detector told about a request that failed to
        # persist would be measuring traffic the audit trail does not show.
        if self._on_written is not None:
            self._on_written(entry.use_case)

    async def _may_store_payloads(self, session: AsyncSession, use_case: str | None) -> bool:
        """Whether this request's bodies may be written at all (FRD-404).

        Two levels, and the installation wins: ``AIRA_STORE_PAYLOADS`` is an operator kill switch,
        so a use-case admin can decline storage but cannot re-enable it where the operator forbade
        it. Requests without a use case fall back to the installation setting.
        """
        if not self._settings.store_payloads:
            return False
        if use_case is None:
            return True
        record = await session.get(UseCaseRead, use_case)
        # A use case the gateway has not heard of yet: store, matching the previous behaviour, and
        # the retention pruner still applies the default period to it.
        return True if record is None else bool(record.store_payloads)
