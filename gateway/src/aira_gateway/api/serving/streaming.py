"""A streamed answer, accounted for however its connection ends (`FRD-128`).

What a stream's ending means is the same on every surface, so it is decided here, once:

    the upstream broke off      settled at what it had reported, recorded with its failure
    the answer was delivered    served — whether or not the upstream ever reported usage
    the caller left             read to its end first (`ADR-0041`), so the vendor says what the
                                answer cost; settled at what it reported, recorded as `499`. Cut
                                short or never reported: counted at the vendor where it can be and
                                settled at that floor — otherwise unknown, and the reservation goes
                                back

A surface writes only its wire format. It opens the dispatch chain through :class:`Opening`, which
waits for a first chunk no longer than a caller can be kept without a byte (`ADR-0046`). Inside
:meth:`StreamAccounting.accounted` it passes each chunk through :meth:`~StreamAccounting.take` and
says whether the stream was :meth:`~StreamAccounting.delivered` or
:meth:`~StreamAccounting.broke_off`; then it returns an :class:`AccountedStream`, which sees the
accounting run however the response ends — finished, cancelled inside the body, cancelled while a
frame was being sent, or cancelled before the body began. The body itself cannot see the last two.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Mapping
from contextlib import ExitStack, asynccontextmanager, suppress
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from aira_common.logging import get_logger
from aira_gateway.api.serving.accounting import Accounting, accounting
from aira_gateway.api.serving.answers import StreamedNotice
from aira_gateway.api.serving.counting import Unreported
from aira_gateway.api.serving.prepare import Prepared
from aira_gateway.api.serving.pump import Pump
from aira_gateway.api.serving.refusals import (
    caller_message,
    refusal_outcome,
    stream_failure_status,
)
from aira_gateway.audio import AudioDigest
from aira_gateway.audit import AuditTrail
from aira_gateway.core.canonical import CanonicalChunk, CanonicalUsage, ToolCallPart
from aira_gateway.explaining import MODEL_HEADER, withhold
from aira_gateway.pipeline.dispatch import Streaming, passed_over_by
from aira_gateway.shielding import uninterrupted
from aira_gateway.state import drains_of, settings_of

_log = get_logger("aira_gateway")


class Opening:
    """The dispatch chain of a streamed request, looking for the candidate whose first chunk opens
    the stream (`ADR-0032`) — and waited for no longer than a caller can be kept without a byte
    (`ADR-0046`).

    The chain runs as a task of its own from the moment it is made. :meth:`within` waits for it;
    what the chain decides in that time is what it always was — a stream, or a refusal the surface
    still answers with a status. A chain that has not decided by then **goes on deciding**: the
    response begins without it, and this object is what the response reads, its first chunk
    arriving whenever a candidate produces one. Until then the chain still moves on from a
    candidate that fails, because nothing of any model has reached the caller; what changed is only
    that a refusal is now the stream's last element rather than its status.

    Iterated like the stream it opens, and closed like it: closing it before the chain has decided
    stops the chain, and closing it after closes the stream the chain opened.
    """

    def __init__(self, chain: Coroutine[Any, Any, Streaming]) -> None:
        self._task: asyncio.Future[Streaming] = asyncio.ensure_future(chain)
        self._streamed: Streaming | None = None
        #: Told which candidate opened the stream, before its first chunk is handed over.
        self.on_open: Callable[[Streaming], None] | None = None

    async def within(self, seconds: float | None) -> Streaming | None:
        """The opened stream, if the chain opened one within ``seconds``; a refusal it raised in
        that time is raised here, where it is still a status. ``None``: still looking. With no
        ``seconds`` — keepalives switched off — the chain is waited for, however long it takes."""
        try:
            done, _ = await asyncio.wait({self._task}, timeout=seconds)
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if not done:
            return None
        self._streamed = self._task.result()
        return self._streamed

    def __aiter__(self) -> Opening:
        return self

    async def __anext__(self) -> CanonicalChunk:
        if self._streamed is None:
            # A refusal the chain raises is raised here, by the reader, as the stream's failure.
            self._streamed = await self._task
            if self.on_open is not None:
                self.on_open(self._streamed)
        return await anext(self._streamed.chunks)

    async def aclose(self) -> None:
        """Stop the chain if it is still looking; close the stream it opened if it found one."""
        if not self._task.done():
            self._task.cancel()
            # Waited for, not awaited: the chain closes the candidate it was opening before this
            # returns, and a cancellation of the caller is still delivered to the caller.
            await asyncio.wait({self._task})
        # `exception()` retrieves it either way, so a refusal nobody read is not reported as lost.
        if not self._task.cancelled() and self._task.exception() is None and self._streamed is None:
            self._streamed = self._task.result()
        if self._streamed is not None:
            close = getattr(self._streamed.chunks, "aclose", None)
            if close is not None:
                with suppress(Exception):
                    await close()


class StreamAccounting:
    """What a streamed answer produced, gathered as it passes, and how its request ended."""

    def __init__(
        self,
        request: Request,
        trail: AuditTrail,
        prepared: Prepared,
        streamed: Streaming | Opening,
        *,
        api: str,
        operation: str,
        started: float,
        shows_reasoning: bool = False,
    ) -> None:
        #: The model that answered, once a candidate has; until then the one asked first.
        self.model = streamed.model if isinstance(streamed, Streaming) else prepared.model
        self.usage: CanonicalUsage | None = None
        self.finish_reason: str | None = None
        self._request = request
        self._trail = trail
        self._prepared = prepared
        self._api = api
        self._operation = operation
        self._started = started
        canonical = prepared.canonical
        self._notice = StreamedNotice(
            prepared.notices,
            structured=canonical is not None and canonical.structured,
        )
        self._parts: list[str] = []
        #: Whether the surface sends the reasoning on: what the row keeps is what the caller got,
        #: and a surface with no field for thoughts drops them (`FRD-145`).
        self._shows_reasoning = shows_reasoning
        self._reasoning: list[str] = []
        self._tool_calls: list[str] = []
        # What the vendor produced, apart from what this gateway added to it — the material for a
        # count when the caller leaves before the vendor says what it used (`counting`).
        self._answer: list[str] = []
        self._calls: list[ToolCallPart] = []
        #: Who answered and with what request — known once a candidate opened the stream.
        self._provider: Any = None
        self._sent: Any = None
        self._opened_in = ""
        self._audio = AudioDigest()
        self._region_seen = False
        self._failure: Exception | None = None
        self._delivered = False
        self._payload: dict[str, Any] | None = None
        if isinstance(streamed, Opening):
            # The response begins before any model answered (`ADR-0046`), so its headers cannot
            # name one: a fallback may yet answer instead of the model asked first.
            withhold(request.state, MODEL_HEADER)
            streamed.on_open = self._opened
            source: AsyncIterator[CanonicalChunk] = streamed
        else:
            self._opened(streamed)
            source = streamed.chunks
        #: The upstream's chunks, read by a task of their own (`pump`): what the vendor sends is
        #: observed as it arrives, and a stream whose caller left can be read to its end.
        self.chunks = Pump(source, self._observe)

    def _opened(self, streamed: Streaming) -> None:
        """A candidate produced the first chunk: it is the model that answers.

        Recorded now, because this is where it is known: the end of the stream is not reached by a
        caller who leaves. The model is the one that answered — for a fallback, not the one the
        caller named.
        """
        self.model = streamed.model
        self._provider = streamed.provider
        self._sent = streamed.sent
        self._opened_in = streamed.region
        self._trail.served_by(streamed.model, streamed.candidate_index)
        self._trail.passed_over(streamed.skipped, streamed.failed)

    @property
    def text(self) -> str:
        """The text the caller has been sent so far, notice included."""
        return "".join(self._parts)

    @property
    def calls(self) -> tuple[ToolCallPart, ...]:
        """The function calls the stream completed so far, in order."""
        return tuple(self._calls)

    def _observe(self, chunk: CanonicalChunk) -> None:
        """What the vendor sent, as it is **read** — whether or not the caller receives it."""
        if chunk.usage is not None:
            # A running total on some dialects, the final figure on others: the last one is it.
            self.usage = chunk.usage
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason
        if chunk.served_region and not self._region_seen:
            # Where the stream was opened (`FRD-115` FR-10). After a regional failover the
            # configured region is the one that refused it, so the first chunk's word is the
            # evidence.
            self._region_seen = True
            self._trail.served_region = chunk.served_region
        self._calls.extend(chunk.tool_calls)
        self._answer.append(chunk.text_delta)

    def take(self, chunk: CanonicalChunk) -> CanonicalChunk:
        """Account for a chunk the caller is sent, and return it as the caller is to receive it."""
        if chunk.tool_calls:
            # The names, so a streamed tool call reaches the audit row like a buffered one.
            self._tool_calls.extend(call.name for call in chunk.tool_calls)
            self._notice.saw_tool_call()
        if self._shows_reasoning and chunk.reasoning_delta:
            self._reasoning.append(chunk.reasoning_delta)
        if chunk.audio_delta is not None:
            self._audio.add(chunk.audio_delta)
        # The audit row accumulates exactly what the caller receives.
        led = self._notice.lead(chunk.text_delta)
        self._parts.append(led)
        return chunk if led == chunk.text_delta else chunk.model_copy(update={"text_delta": led})

    def delivered(self, payload: dict[str, Any] | None = None) -> None:
        """Everything the stream had was handed over. ``payload`` replaces what is stored of the
        answer, for a surface whose terminal event carries more than the text."""
        self._delivered = True
        self._payload = payload

    def broke_off(self, exc: Exception) -> None:
        """The stream ended in a failure after its status had gone out.

        Either the model that answered broke off, or — on a response that had to begin before any
        model answered (`ADR-0046`) — the chain ran out of candidates. The second says whom it
        tried and passed over, as a refusal's row does (`FRD-302` FR-11).
        """
        self._failure = exc
        chain = passed_over_by(exc)
        if chain is not None:
            self._trail.passed_over(list(chain.skipped), list(chain.failed))
        _log.error(
            "upstream_stream_error",
            error=str(exc),
            error_type=type(exc).__name__,
            status=getattr(exc, "status_code", None),
            model=self.model,
        )

    def stored(self) -> dict[str, Any]:
        """What the audit row keeps of the answer: what the caller received."""
        stored: dict[str, Any] = {"text": self.text}
        if self._reasoning:
            # Stored like the answer it came with (`FRD-135` FR-5), under the same gates.
            stored["reasoning"] = "".join(self._reasoning)
        if not self._audio.empty:
            # Described, never kept (`ADR-0026`); the hash covers every chunk.
            stored["audio"] = self._audio.describe()
        return stored

    @asynccontextmanager
    async def accounted(self) -> AsyncIterator[None]:
        """The body runs inside this, and the request is accounted for however the body ends.

        A caller who left before the upstream's end leaves the upstream to be read to it first,
        so the vendor's own figure is what is booked (`ADR-0041`).
        """
        # Held from the drain until the row is written — past the accounting's own exit — so a
        # stopping server waits for this request's row rather than closing the writer under it.
        with ExitStack() as held:
            async with accounting(
                self._request,
                self._trail,
                self._prepared,
                api=self._api,
                operation=self._operation,
                started=self._started,
            ) as acct:
                try:
                    yield
                finally:
                    try:
                        if self._caller_left():
                            held.enter_context(drains_of(self._request).held())
                            await uninterrupted(self._drain())
                    finally:
                        self._report(acct)
                        await uninterrupted(self.chunks.aclose())

    async def never_started(self) -> None:
        """Account for a response whose body never ran: cancelled while its headers were sent.

        The upstream had produced the first chunk — that is what opened the stream — so the request
        reached a model and leaves its row. Nothing reached the caller, and the upstream is read to
        its end as for any caller who left; what it reported is settled, and with nothing reported
        the row says the caller left and the reservation goes back.
        """
        with ExitStack() as held:
            async with accounting(
                self._request,
                self._trail,
                self._prepared,
                api=self._api,
                operation=self._operation,
                started=self._started,
            ) as acct:
                try:
                    held.enter_context(drains_of(self._request).held())
                    await self._drain()
                finally:
                    self._left(acct, self.stored())
            await self.chunks.aclose()

    def _caller_left(self) -> bool:
        """Whether the body ended with the upstream still sending, and nobody to send it to."""
        return not self._delivered and self._failure is None and not self.chunks.finished

    async def _drain(self) -> None:
        """Read the upstream to its end, for the vendor's own figure — bounded by
        `AIRA_DRAIN_LEFT_STREAMS_SECONDS`, and by a stopping server."""
        seconds = settings_of(self._request).drain_left_streams_seconds
        if seconds <= 0:
            return
        ended = await self.chunks.drain(seconds, drains_of(self._request).stopping)
        if not ended:
            _log.info("left_stream_not_read_to_its_end", model=self.model)

    def _report(self, acct: Accounting) -> None:
        """How the stream ended, in the one vocabulary both surfaces record (module docstring)."""
        note = self._notice.outcome()
        if note is not None:
            self._trail.decisions.append(note)
        stored = self._payload if self._payload is not None else self.stored()
        if self._failure is not None:
            if self.usage is not None:
                # Spent, and partly delivered: what the upstream reported is booked, and the row
                # keeps the failure's status rather than claiming the request was served.
                acct.served(self.model, self.usage, stored, self._tool_calls)
            acct.failed(
                stream_failure_status(self._failure),
                refusal_outcome(self._failure),
                caller_message(self._failure),
            )
        elif self._delivered:
            # Served even when the upstream never reported usage — an OpenAI-compatible server
            # may ignore `stream_options` — or a delivered answer would read as a caller who left
            # and would weigh nothing against a request limit.
            acct.served(self.model, self.usage, stored, self._tool_calls)
        else:
            self._left(acct, stored)

    def _left(self, acct: Accounting, stored: dict[str, Any]) -> None:
        """A caller who left: what the vendor reported, or else what can be counted."""
        if self.usage is not None:
            # A report that came with the end of the answer is the vendor's last word; one before
            # it is a running total, short of what the answer went on to cost.
            acct.abandoned(
                self.model,
                self.usage,
                stored,
                self._tool_calls,
                complete=self.finish_reason is not None,
            )
        else:
            acct.left_unreported(self.model, self._unreported(), stored, self._tool_calls)

    def _unreported(self) -> Unreported | None:
        """What the vendor can be asked to count, or ``None`` where the dispatch did not say who
        answered or with what."""
        if self._provider is None or self._sent is None:
            return None
        return Unreported(
            provider=self._provider,
            sent=self._sent,
            answer="".join(self._answer),
            calls=tuple(self._calls),
            region=self._trail.served_region or self._opened_in,
        )


class AccountedStream(StreamingResponse):
    """A streamed response whose request is accounted for however the connection ends.

    The body accounts for itself while it runs. Two endings never reach it: a cancellation while a
    frame was being sent leaves it suspended between two frames — closed whenever the collector
    gets to it, in another task, unshielded — and one while the headers were being sent means it
    never started at all. Both are settled here, after the response is over.
    """

    def __init__(
        self,
        stream: StreamAccounting,
        body: AsyncGenerator[str],
        *,
        media_type: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(body, media_type=media_type, headers=headers)
        self._stream = stream
        self._body = body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await uninterrupted(self._wind_up())

    async def _wind_up(self) -> None:
        never_started = inspect.getasyncgenstate(self._body) == inspect.AGEN_CREATED
        # Runs what the body still has to run, if a frame was in flight when the connection went;
        # a no-op for a body that finished.
        await self._body.aclose()
        if never_started:
            await self._stream.never_started()
