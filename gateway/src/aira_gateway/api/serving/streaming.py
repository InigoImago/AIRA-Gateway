"""A streamed answer, accounted for however its connection ends (`FRD-128`).

What a stream's ending means is the same on every surface, so it is decided here, once:

    the upstream broke off      settled at what it had reported — a floor short of the end — and
                                recorded with its failure; where it had reported nothing and the
                                connection or this gateway's time limit ended it, not the vendor,
                                counted at the vendor first, as for a caller who left (`FRD-408`)
    this gateway failed         ended like the above, with the error the surface would have sent
                                as a status: recorded as `500` / `internal_error`, and counted at
                                the vendor where nothing was reported — this gateway stopped
                                reading, the vendor did not stop answering
    the answer was delivered    served — whether or not the upstream ever reported usage
    the caller left             read to its end first (`ADR-0041`), so the vendor says what the
                                answer cost; settled at what it reported, recorded as `499`. Cut
                                short or never reported: counted at the vendor where it can be and
                                settled at that floor — otherwise unknown, and the reservation goes
                                back

A surface writes only its wire format. It opens the dispatch chain inside
:meth:`StreamAccounting.dispatching`, which accounts for the request from that moment — the chain
calls a model from its first step — through :class:`Opening`, which waits for a first chunk no
longer than a caller can be kept without a byte (`ADR-0046`). From that block it returns an
:class:`AccountedStream`, which takes the accounting over. Inside :meth:`StreamAccounting.accounted`
the body passes each chunk through :meth:`~StreamAccounting.take` and says whether the stream was
:meth:`~StreamAccounting.delivered` or :meth:`~StreamAccounting.broke_off`, and the response sees
the accounting end however it ends — finished, cancelled inside the body, cancelled while a frame
was being sent, or cancelled before the body began. The body itself cannot see the last two.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.types import Receive, Scope, Send

from aira_common.logging import get_logger
from aira_gateway.api.serving.accounting import Accounting, accounting
from aira_gateway.api.serving.answers import StreamedNotice
from aira_gateway.api.serving.counting import Unreported
from aira_gateway.api.serving.prepare import Prepared
from aira_gateway.api.serving.pump import Pump
from aira_gateway.api.serving.refusals import (
    REFUSALS,
    caller_message,
    refusal_outcome,
    stream_failure_status,
)
from aira_gateway.audio import AudioDigest
from aira_gateway.audit import AuditTrail, Outcome, UsageBasis
from aira_gateway.core.canonical import CanonicalChunk, CanonicalUsage, ToolCallPart
from aira_gateway.explaining import MODEL_HEADER, withhold
from aira_gateway.pipeline.dispatch import Streaming, passed_over_by
from aira_gateway.shielding import uninterrupted
from aira_gateway.state import drains_of, settings_of
from aira_gateway.technical import technical_of
from aira_gateway.upstreams.base import UpstreamError

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
        streamed: Streaming | Opening | None = None,
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
        #: The request's technical record (`FRD-630`): the provider's milestones are seen here.
        self._technical = technical_of(request)
        self._payload: dict[str, Any] | None = None
        self._pump: Pump | None = None
        #: The request's accounting, held from :meth:`dispatching` until the response is over.
        self._exits = AsyncExitStack()
        self._account: Accounting | None = None
        #: Whether a response took the accounting over (`AccountedStream`).
        self._handed_over = False
        if streamed is not None:
            self.reads(streamed)

    def reads(self, streamed: Streaming | Opening) -> None:
        """Read ``streamed``: the stream the chain opened, or the chain itself, still looking for a
        first chunk when the response has to begin without one (`ADR-0046`)."""
        if isinstance(streamed, Opening):
            # The response begins before any model answered (`ADR-0046`), so its headers cannot
            # name one: a fallback may yet answer instead of the model asked first.
            withhold(self._request.state, MODEL_HEADER)
            streamed.on_open = self._opened
            source: AsyncIterator[CanonicalChunk] = streamed
        else:
            self._opened(streamed)
            source = streamed.chunks
        self._pump = Pump(source, self._observe)

    @property
    def chunks(self) -> Pump:
        """The upstream's chunks, read by a task of their own (`pump`): what the vendor sends is
        observed as it arrives, and a stream whose caller left can be read to its end."""
        assert self._pump is not None, "a stream is read once its chain has been opened"
        return self._pump

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
        self._technical.mark("provider_first_chunk")
        if chunk.finish_reason:
            self._technical.mark("provider_finished")
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

        Or this gateway failed while it carried the answer — an adapter that could not read a chunk,
        a defect anywhere on the way. The surface ends the stream as it ends one an upstream broke
        off, with its error element, where the failure would otherwise cut the connection and leave
        the caller a response that stops mid-body. A defect is then nobody else's to report: it is
        logged with its traceback under the name every unhandled error has, and marked on the trace
        as one, as it would have been on its way to the server.
        """
        self._failure = exc
        self._technical.failed(exc)
        chain = passed_over_by(exc)
        if chain is not None:
            self._trail.passed_over(list(chain.skipped), list(chain.failed))
        if not isinstance(exc, REFUSALS):
            _log.error(
                "unhandled_error",
                path=self._request.url.path,
                method=self._request.method,
                error_type=type(exc).__name__,
                error=str(exc),
                model=self.model,
                exc_info=exc,
            )
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            return
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
    async def dispatching(self) -> AsyncIterator[None]:
        """The block that opens the chain, accounted for from its first line.

        From here on a model is being called — for as long as the status waits for a first chunk,
        and a thinking model can be silent for all of it. Whatever ends the request before its
        response exists — a refusal while it is still a status, a failure of this gateway's own, a
        stopping server — settles or gives back the reservation and leaves the row, as on every
        other verb, and closes what the chain opened. Left without an exception, the block has
        built the response, which takes the accounting over (`AccountedStream`).
        """
        self._account = await self._exits.enter_async_context(
            accounting(
                self._request,
                self._trail,
                self._prepared,
                api=self._api,
                operation=self._operation,
                started=self._started,
            )
        )
        try:
            yield
        except BaseException as exc:
            if self._pump is not None:
                await uninterrupted(self._pump.aclose())
            await self._leave(exc)
            raise
        if not self._handed_over:
            # No response took it over, so nothing else would end it.
            await self._leave(None)

    def hand_over(self) -> None:
        """A response took the accounting over: it ends it, however the response ends."""
        self._handed_over = True

    @asynccontextmanager
    async def accounted(self) -> AsyncIterator[None]:
        """The body runs inside this, and the request is accounted for however the body ends.

        A caller who left before the upstream's end leaves the upstream to be read to it first,
        so the vendor's own figure is what is booked (`ADR-0041`).
        """
        async with self._held() as acct:
            try:
                yield
            except asyncio.CancelledError, GeneratorExit:
                # The moment the caller went (`FRD-630`) — before the drain, which goes on reading
                # after they have gone, and whatever the upstream did meanwhile.
                self._technical.mark("caller_left")
                raise
            finally:
                try:
                    if self._caller_left():
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
        async with self._held() as acct:
            try:
                self._technical.mark("caller_left")
                await self._drain()
            finally:
                self._left(acct, self.stored())
        await self.chunks.aclose()

    @asynccontextmanager
    async def _held(self) -> AsyncIterator[Accounting]:
        """The accounting :meth:`dispatching` began, ended by whatever ends this block."""
        assert self._account is not None, "a stream is accounted for from its dispatch"
        try:
            yield self._account
        except BaseException as exc:
            await self._leave(exc)
            raise
        await self._leave(None)

    async def _leave(self, exc: BaseException | None) -> None:
        """End the accounting, telling it what ended the request — as an `async with` around it
        would have: nothing, a cancellation, a refusal, or a failure."""
        if exc is None:
            await self._exits.aclose()
        else:
            await self._exits.__aexit__(type(exc), exc, exc.__traceback__)

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
                if self.finish_reason is None:
                    # Reported before the end: the vendor's running total, a floor (`UsageBasis`).
                    acct.usage_basis = UsageBasis.PARTIAL
            elif _vendor_may_go_on(self._failure):
                # Nothing reported, and the stream ended by the connection, this gateway's time
                # limit or a failure of its own rather than by the vendor, which goes on answering
                # — and billing — on its side. Counted there, as a caller who left is, or the
                # request is booked as free.
                acct.unreported = self._unreported()
            acct.failed(*_recorded_as(self._failure))
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


def _vendor_may_go_on(failure: Exception) -> bool:
    """Whether a stream ended by ``failure`` may still be answered — and billed — at the vendor.

    A transport failure carries no status: this gateway stopped waiting, or the connection broke,
    and either way the vendor was not the one that stopped — nor where this gateway failed while it
    read the answer. An error the vendor sent carries its status, and is the vendor ending the
    answer itself.
    """
    if not isinstance(failure, REFUSALS):
        return True
    return isinstance(failure, UpstreamError) and failure.status_code is None


def _recorded_as(failure: Exception) -> tuple[int, Outcome, str | None]:
    """The status, outcome and message of a stream that failed after its status went out.

    A refusal's own, as both surfaces map it. Anything else is this gateway failing, recorded as
    what it would have been as a status: `500`, `internal_error`, and no message — the caller is
    told no more than that it failed.
    """
    if isinstance(failure, REFUSALS):
        return stream_failure_status(failure), refusal_outcome(failure), caller_message(failure)
    return 500, Outcome.INTERNAL_ERROR, None


class AccountedStream(StreamingResponse):
    """A streamed response whose request is accounted for however the connection ends.

    Built, it takes over the accounting the block that opened the chain began
    (`StreamAccounting.dispatching`). The body accounts for itself while it runs. Two endings never
    reach it: a cancellation while a frame was being sent leaves it suspended between two frames —
    closed whenever the collector gets to it, in another task, unshielded — and one while the
    headers were being sent means it never started at all. Both are settled here, after the response
    is over.
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
        stream.hand_over()

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
