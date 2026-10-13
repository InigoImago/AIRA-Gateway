"""Work that must finish even when the request that started it is cancelled.

A request's exit — settling or giving back its reservation, writing its audit row — is the work a
dropped connection most needs done, and the dropped connection is what cancels it. Two kinds of
cancellation reach that exit, and `asyncio.shield` alone keeps out only one of them:

- **anyio's** is *level-triggered*. Starlette runs a streamed body in a task group and cancels the
  group's scope when the client disconnects (ASGI spec < 2.4, which is what uvicorn speaks), and
  every await inside a cancelled scope is cancelled again — the await on a shielded task included.
  Control then leaves the exit while the work it started is still running, and whatever runs next
  (`BudgetService.hold` releasing an unresolved reservation) races it.
- **asyncio's** `Task.cancel()` is delivered once, and an anyio scope cannot keep it out.

So the work runs as its own task, the wait for it sits in a shielded anyio scope, and a native
cancellation that arrives anyway is held back until the work is done, then delivered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import anyio


async def uninterrupted[T](work: Coroutine[Any, Any, T]) -> T:
    """Run ``work`` to its end, then deliver any cancellation that arrived meanwhile.

    Only for work that ends on its own — a write, a release — since nothing can stop it once it
    has begun. Whatever ``work`` raises is raised here.
    """
    task = asyncio.ensure_future(work)
    cancelled: asyncio.CancelledError | None = None
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                # `wait` rather than `await task`: cancelling a task that awaits another cancels
                # the awaited one too, which is the work this exists to finish.
                await asyncio.wait((task,))
            except asyncio.CancelledError as exc:
                cancelled = exc
    if cancelled is not None:
        # The cancellation is what the caller is owed; a failure of the work rides along on it
        # rather than vanishing as a task exception nobody retrieved.
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            raise cancelled from failure
        raise cancelled
    return task.result()
