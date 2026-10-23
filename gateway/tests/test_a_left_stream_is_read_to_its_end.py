"""The upstream's half of a stream is read apart from the caller's (`ADR-0041`, `FRD-128` FR-6).

Several vendors report what an answer used only in its last chunk. A stream closed with its caller
therefore booked nothing those vendors said — on every dialect but Vertex's Gemini, which can be
counted — and a caller who read the answer and hung up before the last chunk was never billed.
These cases hold the reader to what the accounting relies on: every chunk read is seen, whatever
becomes of it; a stream can be read to its end after nobody is reading it; and that read stops at
its bound, at a stopping server, and closes the upstream where it was opened.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from aira_gateway.api.serving.pump import Drains, Pump
from aira_gateway.core.canonical import CanonicalChunk, CanonicalUsage
from aira_gateway.upstreams.base import UpstreamError


def _chunk(text: str, *, usage: int | None = None) -> CanonicalChunk:
    reported = None if usage is None else CanonicalUsage(prompt_tokens=1, completion_tokens=usage)
    return CanonicalChunk(text_delta=text, usage=reported)


async def _answer(quiet: asyncio.Event | None = None) -> AsyncIterator[CanonicalChunk]:
    """An answer whose usage comes only with its last chunk; ``quiet`` holds it before that."""
    yield _chunk("do")
    yield _chunk("n")
    if quiet is not None:
        await quiet.wait()
    yield _chunk("e", usage=3)


async def test_every_chunk_read_is_observed_whatever_becomes_of_it() -> None:
    """The caller took one chunk and left. The rest, read to its end, is seen all the same —
    including the last, which is the only one that says what the answer cost."""
    seen: list[CanonicalChunk] = []
    pump = Pump(_answer(), seen.append)

    first = await anext(pump)
    ended = await pump.drain(5, asyncio.Event())

    assert first.text_delta == "do"
    assert ended is True
    assert [chunk.text_delta for chunk in seen] == ["do", "n", "e"]
    assert seen[-1].usage is not None and seen[-1].usage.completion_tokens == 3
    assert pump.finished


async def test_a_chunk_a_consumer_dropped_was_still_observed() -> None:
    """A consumer between the reader and the caller — the KIRA keepalive — can be cancelled holding
    a chunk it already took. The accounting saw it when it was read, so nothing is lost there."""
    seen: list[CanonicalChunk] = []
    pump = Pump(_answer(), seen.append)

    async def consumer() -> None:
        async for _ in pump:
            await asyncio.sleep(3600)  # holding the chunk when it is cancelled

    held = asyncio.ensure_future(consumer())
    await asyncio.sleep(0.01)
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    await pump.drain(5, asyncio.Event())

    assert [chunk.text_delta for chunk in seen] == ["do", "n", "e"]


async def test_a_read_on_stops_when_the_server_stops() -> None:
    quiet = asyncio.Event()
    pump = Pump(_answer(quiet), lambda chunk: None)
    stop = asyncio.Event()

    asyncio.get_running_loop().call_later(0.05, stop.set)
    ended = await asyncio.wait_for(pump.drain(600, stop), timeout=5)

    assert ended is False, "a stopped read is not an answer read to its end"
    await pump.aclose()


async def test_a_read_on_ends_at_its_bound() -> None:
    quiet = asyncio.Event()
    pump = Pump(_answer(quiet), lambda chunk: None)

    ended = await asyncio.wait_for(pump.drain(0.05, asyncio.Event()), timeout=5)

    assert ended is False
    await pump.aclose()


async def test_a_reader_that_never_started_is_started_by_the_read_on() -> None:
    """A caller can leave before the first chunk was read: the read on starts the reader."""
    seen: list[CanonicalChunk] = []
    pump = Pump(_answer(), seen.append)

    assert await pump.drain(5, asyncio.Event()) is True
    assert len(seen) == 3


async def test_the_upstream_is_closed_inside_the_reader_that_opened_it() -> None:
    """An upstream generator closed from another task resets its context variables in a context
    that never set them; closed by cancelling its reader, it unwinds where it was advanced."""
    advanced_in: list[asyncio.Task[object] | None] = []
    closed_in: list[asyncio.Task[object] | None] = []

    async def upstream() -> AsyncIterator[CanonicalChunk]:
        try:
            advanced_in.append(asyncio.current_task())
            yield _chunk("do")
            await asyncio.Event().wait()
            yield _chunk("ne")  # pragma: no cover - never reached
        finally:
            closed_in.append(asyncio.current_task())

    pump = Pump(upstream(), lambda chunk: None)
    await anext(pump)
    await pump.aclose()

    assert closed_in and closed_in == advanced_in
    assert closed_in[0] is not asyncio.current_task()


async def test_an_upstream_whose_reader_waits_to_hand_over_is_closed_too() -> None:
    """Nobody took the chunk the reader holds, so the reader waits outside the upstream's iterator,
    not inside it: closing the reader must close the upstream as well, or its response stays open
    until the collector finds it."""
    closed: list[bool] = []

    async def upstream() -> AsyncIterator[CanonicalChunk]:
        try:
            for text in ("a", "b", "c"):
                yield _chunk(text)
        finally:
            closed.append(True)

    pump = Pump(upstream(), lambda chunk: None)
    await anext(pump)  # the reader runs ahead and waits to hand the next chunk over
    await asyncio.sleep(0.01)
    await pump.aclose()

    assert closed == [True], "the upstream was left open behind a reader that was closed"


async def test_an_upstream_failure_is_raised_where_it_is_read() -> None:
    async def failing() -> AsyncIterator[CanonicalChunk]:
        yield _chunk("do")
        raise UpstreamError("the provider went away", 503)

    pump = Pump(failing(), lambda chunk: None)

    with pytest.raises(UpstreamError):
        async for _ in pump:
            pass
    assert pump.finished


async def test_a_failure_ends_a_read_on_as_no_answer() -> None:
    async def failing() -> AsyncIterator[CanonicalChunk]:
        yield _chunk("do")
        raise UpstreamError("the provider went away", 503)

    pump = Pump(failing(), lambda chunk: None)

    assert await pump.drain(5, asyncio.Event()) is False


async def test_a_stop_waits_for_the_requests_it_cut_short_and_no_longer() -> None:
    drains = Drains()
    finished = asyncio.Event()

    async def request() -> None:
        with drains.held():
            await drains.stopping.wait()
            await asyncio.sleep(0.05)  # booking and recording what it knows
            finished.set()

    running = asyncio.ensure_future(request())
    await asyncio.sleep(0.01)
    await drains.stop(seconds=5)

    assert finished.is_set(), "the stop returned before the request it cut short had its row"
    await running


async def test_a_stop_with_nothing_reading_returns_at_once() -> None:
    await asyncio.wait_for(Drains().stop(seconds=60), timeout=1)


async def test_an_answer_read_to_its_end_stays_at_its_end() -> None:
    """Asked again after its end — a second read on, a surface iterating twice — the reader answers
    from what it knows rather than waiting on a queue nothing will fill."""
    pump = Pump(_answer(), lambda chunk: None)
    _ = [chunk async for chunk in pump]

    assert await asyncio.wait_for(pump.drain(5, asyncio.Event()), timeout=1) is True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(pump), timeout=1)


async def test_a_failed_answer_stays_failed() -> None:
    async def failing() -> AsyncIterator[CanonicalChunk]:
        raise UpstreamError("the provider went away", 503)
        yield  # pragma: no cover - makes this an async generator

    pump = Pump(failing(), lambda chunk: None)
    assert await pump.drain(5, asyncio.Event()) is False

    assert await asyncio.wait_for(pump.drain(5, asyncio.Event()), timeout=1) is False


class _Iterator:
    """An upstream iterator that is not a generator: nothing to close."""

    def __aiter__(self) -> _Iterator:
        return self

    async def __anext__(self) -> CanonicalChunk:
        raise StopAsyncIteration


class _Unclosable(_Iterator):
    async def aclose(self) -> None:
        raise RuntimeError("the connection was already gone")


@pytest.mark.parametrize("source", [_Iterator(), _Unclosable()], ids=["no-aclose", "aclose-fails"])
async def test_closing_an_upstream_never_raises(source: _Iterator) -> None:
    """A failure to close is logged: it must not cost the request its row."""
    await Pump(source, lambda chunk: None).aclose()  # never started
    started = Pump(source, lambda chunk: None)
    assert await started.drain(5, asyncio.Event()) is True
    await started.aclose()


async def test_a_stop_that_waits_in_vain_returns_at_its_bound() -> None:
    """A request that does not finish holds no shutdown for longer than the bound."""
    drains = Drains()
    never = asyncio.Event()

    async def stuck() -> None:
        with drains.held():
            await never.wait()

    running = asyncio.ensure_future(stuck())
    await asyncio.sleep(0.01)
    await asyncio.wait_for(drains.stop(seconds=0.05), timeout=1)

    assert drains._active == 1  # noqa: SLF001 - what the warning at the stop reports
    never.set()
    await running
