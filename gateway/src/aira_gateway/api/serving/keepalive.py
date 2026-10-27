"""A streamed response that keeps its connection while the model is quiet (`ADR-0046`).

A model can say nothing for minutes: it reads a large context, it thinks, it writes a function
call's arguments, and none of that is text a caller is sent. A proxy between the caller and this
gateway closes a connection that carried nothing for its idle timeout — nginx's default is sixty
seconds — and a client waiting for a response's headers gives up after its own. Either way the
caller is gone, and the request is recorded as a caller who left (`499`), although the caller was
waiting for the gateway and not the other way round.

So a streamed response is never silent for longer than `AIRA_STREAM_KEEPALIVE_SECONDS`: its
headers go out within that long even while the dispatch chain is still looking for a first chunk
(`streaming.Opening`), and from then on each surface sends its own keepalive — something its
clients skip — whenever nothing else went out for that long. What a surface sends is its own
business, because what its clients skip differs: an SSE comment for KIRA's clients, a bare line
for Google's (`api.gemini.routes`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, Final

from fastapi import Request

from aira_gateway.state import settings_of

#: What `with_pings` yields in place of an item when the interval passed without one.
TICK: Final = object()

#: Response headers that stop a reverse proxy from buffering the stream until it ends — nginx reads
#: this one, and a buffered stream is one answer delivered late rather than a stream, with every
#: keepalive held back with it.
STREAM_HEADERS = {"X-Accel-Buffering": "no"}


def keepalive_seconds(request: Request) -> float | None:
    """How long a stream may stay silent here, or ``None`` where it sends nothing to keep its
    connection (`AIRA_STREAM_KEEPALIVE_SECONDS=0`)."""
    return settings_of(request).stream_keepalive_seconds or None


async def with_pings[Item](
    items: AsyncIterator[Item], interval: float | None
) -> AsyncIterator[Item | object]:
    """``items``, with :data:`TICK` in between whenever ``interval`` passes without one — and
    without any where ``interval`` is ``None``.

    ``items`` is read **by one task of its own, to its end**, and handed over through a queue of
    one. Not by a fresh task per item: a generator advanced from several tasks resets a context
    variable in a context that never set it. And never by cancelling a slow read, which would close
    the model's answer at its first quiet moment. Closing this iterator — a caller who left —
    cancels the reader and leaves ``items`` as it is: whether the upstream is then read to its end
    or closed is the stream's accounting to decide (`api.serving.pump`).
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
