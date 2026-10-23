"""The upstream's half of a stream, read apart from the caller's (`FRD-128` FR-6, `ADR-0041`).

A caller who leaves cancels the body that writes to them, and a read cancelled in the middle closes
the vendor's answer before the vendor said what it cost: several dialects report usage only in a
stream's last chunk. So the upstream is read by **one task of its own** (:class:`Pump`), every
chunk is shown to the accounting the moment it arrives — whatever becomes of it after — and a
stream whose caller left can be read to its end (:meth:`Pump.drain`), for the vendor's own figure.

One task, never one per chunk: a generator advanced from several tasks resets a context variable in
a context that never set it, and closing the reader's task is what closes the upstream's response
inside the task that opened it.

A drain holds its request open after the caller has gone, so a stopping server must be able to cut
it short: :class:`Drains` counts the requests reading to their end, and the application's shutdown
stops them — each then books what it knows and writes its row — before the audit writer closes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any, Final

from aira_common.logging import get_logger
from aira_gateway.core.canonical import CanonicalChunk

_log = get_logger("aira_gateway")

#: How far the reader may run ahead of the caller: one chunk, as when the caller read the upstream
#: directly — the vendor's pace stays the caller's while the caller is there.
PUMP_DEPTH = 1

#: How long a stopping server waits for the requests it cut short to book and record themselves.
#: They have stopped reading, so what is left is a settlement and a row each.
DRAINS_STOP_SECONDS = 3.0

_CHUNK: Final = "chunk"
_FAILED: Final = "failed"
_END: Final = "end"


class Pump:
    """The upstream's chunks, read by one task of its own and handed over through a queue.

    Iterated like the upstream's own iterator: the same chunks, the same failure where it fails.
    ``observe`` sees each chunk as it is **read** — before it is handed over, and whether or not it
    ever is — so nothing the vendor sent is missed by the accounting because the caller left
    between two chunks.
    """

    def __init__(
        self,
        source: AsyncIterator[CanonicalChunk],
        observe: Callable[[CanonicalChunk], None],
    ) -> None:
        self._source = source
        self._observe = observe
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=PUMP_DEPTH)
        self._task: asyncio.Task[None] | None = None
        #: The upstream's last word, once it was handed over: ``_END`` or ``_FAILED``.
        self._outcome: str | None = None

    @property
    def finished(self) -> bool:
        """Whether the upstream's end — or its failure — has been handed over."""
        return self._outcome is not None

    def __aiter__(self) -> Pump:
        return self

    async def __anext__(self) -> CanonicalChunk:
        if self._outcome is not None:
            raise StopAsyncIteration
        kind, value = await self._next()
        if kind == _END:
            raise StopAsyncIteration
        if kind == _FAILED:
            raise value
        chunk: CanonicalChunk = value
        return chunk

    async def drain(self, seconds: float, stop: asyncio.Event) -> bool:
        """Read what is left, up to the upstream's end: whether it ended, rather than failing,
        running out of ``seconds`` or being ``stop``ped.

        Every chunk is observed as it is read; none is handed over, because nobody is there to take
        it. A reader that is not running yet is started — a caller can leave before the first chunk
        was read.
        """
        if self._outcome is not None:
            return self._outcome == _END
        stopped = asyncio.ensure_future(stop.wait())
        try:
            async with asyncio.timeout(seconds):
                while True:
                    item = asyncio.ensure_future(self._next())
                    await asyncio.wait({item, stopped}, return_when=asyncio.FIRST_COMPLETED)
                    if not item.done():
                        # Stopped. The item is abandoned, not lost: the reader is closed next.
                        item.cancel()
                        with suppress(asyncio.CancelledError):
                            await item
                        return False
                    kind, _ = item.result()
                    if kind != _CHUNK:
                        return kind == _END
        except TimeoutError:
            return False
        finally:
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped

    async def aclose(self) -> None:
        """Stop reading and hand the upstream's connection back — inside the reader's own task,
        where the upstream's response was opened."""
        task = self._task
        if task is None:
            await _closed(self._source)
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _next(self) -> tuple[str, Any]:
        if self._task is None:
            self._task = asyncio.ensure_future(self._read())
        kind, value = await self._queue.get()
        if kind != _CHUNK:
            self._outcome = kind
        return kind, value

    async def _read(self) -> None:
        try:
            async for chunk in self._source:
                self._observe(chunk)
                await self._queue.put((_CHUNK, chunk))
        except Exception as exc:  # noqa: BLE001 — handed over, and raised where it is read
            await self._queue.put((_FAILED, exc))
        else:
            await self._queue.put((_END, None))
        finally:
            await _closed(self._source)


async def _closed(source: AsyncIterator[CanonicalChunk]) -> None:
    """Close the upstream's iterator. A failure to close is logged and never raised: it must not
    cost the request its row."""
    close = getattr(source, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001 — see above
        _log.warning("stream_not_closed", exc_info=True)


class Drains:
    """The requests still reading an upstream their caller left, and the signal that stops them."""

    def __init__(self) -> None:
        #: Set when the application stops: every drain returns, and its request books what it knows.
        self.stopping = asyncio.Event()
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @contextmanager
    def held(self) -> Iterator[None]:
        """Count one request from its drain to its row, so a stop can wait for it."""
        self._active += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def stop(self, seconds: float = DRAINS_STOP_SECONDS) -> None:
        """Cut every drain short, and wait — at most ``seconds`` — for their requests to be
        accounted for, so their rows reach the audit writer before it closes."""
        self.stopping.set()
        with suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._idle.wait()
        if self._active:
            _log.warning("stream_drains_unfinished_at_stop", count=self._active)
