"""Writing the request log off the request path (`FRD-405` §4.4).

Persistence must not block the request path, so audit rows go through a **bounded** queue to a
background worker — an unbounded one would only move the exhaustion from the connection pool to
memory. Bounded twice: in rows, and in the bodies those rows hold (:data:`MAX_QUEUED_BYTES`). A full
queue writes **inline** rather than dropping the entry: backpressure lands on the caller producing
the load, and the request log never silently loses rows.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.attachments import strip_attachments
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.base import new_id
from aira_gateway.db.errors import loggable
from aira_gateway.db.models import UseCaseRead
from aira_gateway.db.transient import is_transient
from aira_gateway.persistence.model_state import without_model_state
from aira_gateway.persistence.redaction import Redactor
from aira_gateway.persistence.sanitize import (
    MAX_STORED_DEPTH,
    NOT_AN_OBJECT_KEY,
    TOO_DEEP,
    as_object,
    storable,
    within_depth,
)
from aira_gateway.persistence.service import RequestLogService

__all__ = [
    "MAX_STORED_DEPTH",
    "NOT_AN_OBJECT_KEY",
    "TOO_DEEP",
    "PendingLog",
    "RequestLogWriter",
    "as_object",
    "storable",
    "within_depth",
]

_log = get_logger("aira_gateway.persistence")

#: The most of request and response bodies the queue holds, in bytes, however many rows that is.
#: Bounded in rows alone, a slow database would keep up to `AIRA_LOG_QUEUE_SIZE` bodies of up to
#: 8 MiB each in memory — gigabytes, and a process killed for its memory loses every row still
#: queued. Past it a row is written inline, the backpressure a full queue already applies.
MAX_QUEUED_BYTES = 64 * 2**20

#: What a value in a body is reckoned at beside its text: an object, a list, a number.
_VALUE_BYTES = 64


@dataclass(frozen=True, slots=True)
class PendingLog:
    """One audit row, captured on the request path and written afterwards.

    Everything only the live request can tell — attribution, source IP, trace id — is resolved
    before this is handed over; the worker never touches the request.
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
    #: Which surface this row belongs to. **No default**: a discriminator with a default is right
    #: on one surface and silently wrong on every other.
    api: str
    # Defaulted from here on: a refusal, or the body-size middleware's own row, often knows nothing
    # more (`FRD-122`).
    credential: str | None = None
    #: Which realm minted the token (`FRD-118`).
    issuer: str | None = None
    #: A name for grouping a display, not an identity (`FRD-606`).
    username: str | None = None
    outcome: str | None = None
    #: How the usage was established where it is not the vendor's final report (`UsageBasis`).
    usage_basis: str | None = None
    requested_model: str | None = None
    model_selection: str | None = None
    pipeline_decisions: list[dict[str, Any]] | None = None
    #: A pipeline step objected to this request — blocked it, or flagged it and let it through.
    flagged: bool = False
    tool_calls: dict[str, Any] | None = None
    degraded: dict[str, str] | None = None
    #: The request's technical record (`technical`, `FRD-630`): codes and numbers only.
    technical: dict[str, Any] | None = None
    provider: str | None = None
    publisher: str | None = None
    region: str | None = None
    #: Bytes the caller sent (`FRD-501`). NULL where unknown, never 0: an unknown size must not
    #: look like a small one.
    request_bytes: int | None = None
    #: The id the caller was given in `X-AIRA-Request-Id` (`FRD-628` FR-1); `None` draws one.
    row_id: str | None = None
    #: The message the caller was given, where the request did not succeed (`FRD-628` FR-4).
    error: str | None = None


#: What a request's line is, for a SIEM that filters by kind.
REQUEST_CATEGORY = "request"


def say_recorded(entry: PendingLog) -> None:
    """One line for one audit row: who asked which model through which use case, and how it ended.

    The row's metadata, never its bodies: the prompt and the answer stay behind the storage switch,
    the retention clock and the role check, none of which a log line has (`ADR-0016`). The person
    and the address are written by the pipeline's rules — a pseudonym under strict data mode, the
    address as `AIRA_TELEMETRY_IP` says — so the line names nobody the audit row's readers do not.
    Re-identifying the caller is the audit trail's: the line carries the request's id, which the
    console finds.
    """
    usage = entry.usage
    tools = entry.tool_calls or {}
    fields: dict[str, Any] = {
        "category": REQUEST_CATEGORY,
        "request_id": entry.row_id,
        "trace_id": entry.trace_id,
        "api": entry.api,
        "operation": entry.operation,
        "use_case": entry.use_case,
        "subject": entry.subject,
        "auth_method": entry.auth_method,
        "credential": entry.credential,
        "issuer": entry.issuer,
        "model": entry.model,
        "requested_model": entry.requested_model,
        "status": entry.status,
        "outcome": entry.outcome,
        "input_tokens": usage.prompt_tokens if usage is not None else None,
        "output_tokens": usage.completion_tokens if usage is not None else None,
        "total_tokens": usage.total_tokens if usage is not None else None,
        "usage_basis": entry.usage_basis,
        "cost_nanos": entry.cost_nanos,
        "latency_ms": entry.latency_ms,
        "provider": entry.provider,
        "publisher": entry.publisher,
        "region": entry.region,
        "tools_offered": int(tools.get("declared") or 0) if tools else None,
        "tools_called": len(tools.get("called") or []) if tools else None,
        "flagged": entry.flagged or None,
        "request_bytes": entry.request_bytes,
        "source_ip": entry.source_ip,
    }
    _log.info(
        "request_recorded", **{key: value for key, value in fields.items() if value is not None}
    )


def held_bytes(entry: PendingLog) -> int:
    """About how much memory ``entry``'s bodies keep while it waits in the queue.

    The request body at the bytes the caller sent, which the body-size middleware counted as they
    arrived — weighing the parsed body instead would walk up to 8 MiB of it on the event loop —
    and walked only where no count was made. The answer by a walk over what the gateway built of
    it: a few objects, as long as the model's output.
    """
    request = 0
    if entry.request_payload is not None:
        counted = entry.request_bytes
        request = counted if counted is not None else _weight(entry.request_payload)
    return request + _weight(entry.response_payload)


def _weight(payload: Any) -> int:
    """The length of every string in ``payload``, keys included, and :data:`_VALUE_BYTES` for
    every other value. Iterative, so a deeply nested answer cannot exhaust the stack."""
    total = 0
    pending: list[Any] = [payload]
    while pending:
        item = pending.pop()
        if isinstance(item, str | bytes | bytearray):
            total += len(item)
        elif isinstance(item, dict):
            total += _VALUE_BYTES
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            total += _VALUE_BYTES
            pending.extend(item)
        elif item is not None:
            total += _VALUE_BYTES
    return total


class RequestLogWriter:
    """Buffers audit rows and writes them from a background worker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Any,
        redactor: Redactor,
        *,
        max_queue: int = 512,
        max_queue_bytes: int = MAX_QUEUED_BYTES,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._redactor = redactor
        self._max_queue = max_queue
        self._max_bytes = max_queue_bytes
        # Each entry with the bytes it was admitted at, given back once it is written.
        self._queue: asyncio.Queue[tuple[PendingLog, int]] = asyncio.Queue(
            maxsize=max(1, max_queue)
        )
        self._held = 0
        self._worker: asyncio.Task[None] | None = None
        # Set when a shutdown *begins*: `stop()` awaits the worker, and a row submitted during that
        # await must be written inline rather than queued against a worker being cancelled.
        self._stopping = False
        self.written_inline = 0

    async def start(self) -> None:
        """Start the worker. A queue size of zero keeps writing on the request path.

        A supported configuration: an operator who needs each row durable before its response is
        returned can ask for it, at the cost of the latency this removes.
        """
        if self._max_queue <= 0:
            return
        self._stopping = False
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def drain(self) -> None:
        """Wait until everything queued has been written — or until the worker has stopped.

        Only the worker marks entries done, so after it dies `join()` alone would wait for ever: a
        defect in the write path would show up as a hang rather than as the missing rows it is.
        The same race :meth:`stop` runs.
        """
        if self._worker is None:
            return
        drained = asyncio.create_task(self._queue.join())
        await asyncio.wait({drained, self._worker}, return_when=asyncio.FIRST_COMPLETED)
        if not drained.done():
            drained.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drained

    @property
    def synchronous(self) -> bool:
        """Whether every row is written before its response: a queue size of zero, chosen by an
        installation that needs each row durable first (see :meth:`start`)."""
        return self._max_queue <= 0

    @property
    def pending(self) -> int:
        """Rows accepted but not yet written. A rising figure means the database is slower than
        the traffic, and the inline fallback is about to apply backpressure."""
        return self._queue.qsize()

    async def stop(self) -> None:
        """Drain what is queued, then stop. A redeploy must not discard pending audit rows.

        The drain **races the worker**: only the worker marks entries done, so if it has died — an
        outside cancellation, a `BaseException` from the write path — `join()` would wait forever
        and shutdown would end in `SIGKILL` with the queue lost. If the worker finishes first,
        whatever is still queued is written here.
        """
        if self._worker is None:
            return
        self._stopping = True
        drained = asyncio.create_task(self._queue.join())
        await asyncio.wait({drained, self._worker}, return_when=asyncio.FIRST_COMPLETED)
        if not drained.done():
            drained.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drained
            await self._write_remaining()
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    async def _write_remaining(self) -> None:
        """Write what is still queued, on this task, because the worker is not coming back.

        Failures are logged, not raised: one bad row must not stop the rest during shutdown.
        """
        while True:
            try:
                entry, weight = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self._write(entry)
            except Exception as exc:  # noqa: BLE001 — see above
                _log.error(
                    "request_log_write_failed_during_shutdown",
                    error=loggable(exc),
                    error_type=type(exc).__name__,
                    operation=entry.operation,
                )
            finally:
                self._held -= weight
                self._queue.task_done()

    async def submit(self, entry: PendingLog) -> None:
        """Hand an entry over; falls back to writing it here if the queue is saturated.

        **Every row is also a line** (`FRD-631` FR-11), said here because every row passes here:
        a SIEM fed with logs had no record of any request — the row is a database write and the
        span is not exported everywhere. Said before the row is written, so a row the database
        refuses is still a request somebody can see happened.
        """
        say_recorded(entry)
        if not self._settings.store_payloads and (
            entry.request_payload is not None or entry.response_payload is not None
        ):
            # Never stored, so never held: the installation's switch is known here, and a body
            # kept until the worker drops it is memory the queue holds for nothing. A use case's
            # own switch arrives with its row, which only the worker reads.
            entry = replace(entry, request_payload=None, response_payload=None)
        if self._worker is None or self._stopping:
            # No worker, or one on its way out: write it here rather than queue it into nothing.
            await self._write(entry)
            return
        try:
            self._admit(entry)
        except asyncio.QueueFull:
            self.written_inline += 1
            _log.warning("request_log_queue_full", operation=entry.operation)
            await self._write(entry)

    def _admit(self, entry: PendingLog) -> None:
        """Queue ``entry``, or raise :class:`asyncio.QueueFull` when either bound would be passed:
        the rows, or the bodies they hold. A row without a body weighs nothing, so the second bound
        never turns one away."""
        weight = held_bytes(entry)
        if self._held + weight > self._max_bytes:
            raise asyncio.QueueFull
        self._queue.put_nowait((entry, weight))
        self._held += weight

    async def _run(self) -> None:
        while True:
            entry, weight = await self._queue.get()
            try:
                await self._write(entry)
            except Exception as exc:  # a failed write must never take the worker down with it
                _log.error(
                    "request_log_write_failed",
                    error=loggable(exc),
                    error_type=type(exc).__name__,
                    operation=entry.operation,
                )
            finally:
                self._held -= weight
                self._queue.task_done()

    async def _write(self, entry: PendingLog) -> None:
        """Write ``entry``'s row, and **once more if the database failed it for a reason of its
        own** (:func:`is_transient`).

        A database that restarted or failed over leaves connections the server has closed, and
        the row that meets one would be lost with nothing wrong with it. The pool tests a
        connection before handing it out, which leaves the moment between that test and the
        statement; the second attempt takes a fresh connection. The row's id is fixed before the
        first, so a first attempt that did land after all fails the second on its key rather than
        leaving two rows.
        """
        if entry.row_id is None:
            entry = replace(entry, row_id=new_id())
        try:
            await self._write_once(entry)
        except Exception as exc:
            if not is_transient(exc):
                raise
            _log.warning(
                "request_log_write_retried",
                error_type=type(exc).__name__,
                operation=entry.operation,
            )
            await self._write_once(entry)

    async def _write_once(self, entry: PendingLog) -> None:
        async with self._sessionmaker() as session:
            store = await self._may_store_payloads(session, entry.use_case)

            def _maybe(payload: dict[str, Any] | None) -> dict[str, Any] | None:
                if not store or payload is None:
                    return None
                # An object before anything walks it. A list or a string is wrapped here, not after
                # the redactor: a redactor is handed the mapping its signature names, and one handed
                # anything else returned it unredacted.
                payload = as_object(payload)
                # Bound the structure first: each walk below recurses (see `within_depth`).
                bounded: dict[str, Any] = within_depth(payload)
                # Strip, then redact: binary the gateway never inspected stays out of the row and
                # out of the redactor (`FRD-110` §5.4), and so does a model's signature, which is
                # state rather than anything said. Unconditional, so swapping the redactor cannot
                # turn it off.
                stripped: dict[str, Any] = without_model_state(strip_attachments(bounded))
                # `storable` last, so it also covers whatever a redactor substitutes in.
                redacted: dict[str, Any] = self._redactor.redact(stripped)
                return as_object(storable(redacted))

            # On a worker thread: these walks are pure, and over a body of up to 8 MiB they take a
            # few hundred milliseconds that every request on this event loop would wait for. Off
            # it, the loop is held at most for one regular expression's pass.
            request_payload, response_payload = await asyncio.to_thread(
                lambda: (_maybe(entry.request_payload), _maybe(entry.response_payload))
            )
            await RequestLogService(session).record(
                subject=entry.subject,
                username=entry.username,
                auth_method=entry.auth_method,
                use_case=entry.use_case,
                source_ip=entry.source_ip,
                operation=entry.operation,
                model=entry.model,
                status=entry.status,
                usage=entry.usage,
                latency_ms=entry.latency_ms,
                trace_id=entry.trace_id,
                request_payload=request_payload,
                response_payload=response_payload,
                cost_nanos=entry.cost_nanos,
                credential=entry.credential,
                issuer=entry.issuer,
                outcome=entry.outcome,
                usage_basis=entry.usage_basis,
                requested_model=entry.requested_model,
                model_selection=entry.model_selection,
                pipeline_decisions=entry.pipeline_decisions,
                flagged=entry.flagged,
                tool_calls=entry.tool_calls,
                degraded=entry.degraded,
                technical=entry.technical,
                provider=entry.provider,
                publisher=entry.publisher,
                region=entry.region,
                api=entry.api,
                request_bytes=entry.request_bytes,
                row_id=entry.row_id,
                # Through the same redactor as the payloads: an upstream's error text can quote
                # what it was sent, and a credential in it must not reach the row.
                error=self._redacted_message(entry.error),
            )

    def _redacted_message(self, message: str | None) -> str | None:
        if not message:
            return None
        redacted = self._redactor.redact({"error": message})
        value = redacted.get("error") if isinstance(redacted, dict) else None
        return value if isinstance(value, str) else None

    async def _may_store_payloads(self, session: AsyncSession, use_case: str | None) -> bool:
        """Whether this request's bodies may be written at all (`FRD-404`).

        The installation wins: ``AIRA_STORE_PAYLOADS`` is an operator kill switch a use case cannot
        override. Requests without a use case follow the installation setting.
        """
        if not self._settings.store_payloads:
            return False
        if use_case is None:
            return True
        record = await session.get(UseCaseRead, use_case)
        # A use case not yet heard of has said nothing about its content, and a use case that
        # switched storage off says it in the same row that has not arrived: nothing is written.
        return False if record is None else bool(record.store_payloads)
