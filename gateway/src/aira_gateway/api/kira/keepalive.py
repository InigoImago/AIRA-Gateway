"""A stream that keeps its connection while the model is quiet (`FRD-146` FR-19).

A thinking model can say nothing for a minute before its first word, and a proxy between the caller
and this gateway closes a connection that carried nothing for its idle timeout — nginx's default is
sixty seconds. The predecessor sends an SSE **comment** every fifteen seconds: a client ignores it
(it is not an event, so no `update` without text reaches anybody), and the connection stays open.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, Final

#: How long the stream may stay silent before a comment is sent.
PING_INTERVAL_SECONDS = 15.0

#: The comment itself: a line starting with a colon, which SSE defines as ignored.
PING = ": ping\n\n"

#: What `with_pings` yields in place of an item when the interval passed without one.
TICK: Final = object()

#: Response headers that stop a reverse proxy from buffering the stream until it ends — nginx reads
#: this one, and a buffered stream is one answer delivered late rather than a stream.
STREAM_HEADERS = {"X-Accel-Buffering": "no"}


async def with_pings[Item](
    items: AsyncIterator[Item], interval: float = PING_INTERVAL_SECONDS
) -> AsyncIterator[Item | object]:
    """``items``, with :data:`TICK` in between whenever ``interval`` passes without one.

    ``items`` is read **by one task of its own, to its end**, and handed over through a queue of
    one. Not by a fresh task per item: a generator advanced from several tasks resets a context
    variable in a context that never set it. And never by cancelling a slow read, which would close
    the model's answer at its first quiet moment. Closing this iterator — a caller who left —
    cancels the reader, which closes ``items`` as closing it directly would have.
    """
    queue: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue(maxsize=1)

    async def read() -> None:
        try:
            async for item in items:
                await queue.put((False, item))
        except Exception as exc:  # noqa: BLE001 — handed to the reader, which raises it
            await queue.put((True, exc))
            return
        await queue.put((True, None))

    reader = asyncio.ensure_future(read())
    try:
        while True:
            try:
                finished, value = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield TICK
                continue
            if finished:
                if value is not None:
                    raise value
                return
            yield value
    finally:
        reader.cancel()
        with suppress(asyncio.CancelledError):
            await reader
