"""The ASGI lifespan Django does not speak, so Management can say it stops (`FRD-631` FR-4).

Django's ASGI application answers no `lifespan` scope, so uvicorn wrote "ASGI 'lifespan' protocol
appears unsupported" at every start and stopped the process without telling it: no line said the
service stopped, and what the export still held was left to the interpreter's exit. This answers
the protocol and passes every other scope to Django unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from aira_common.logging.lifecycle import say_stopped, say_stopping
from aira_common.observability import flush_telemetry

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def with_lifespan(app: ASGIApp, *, service: str) -> ASGIApp:
    """``app`` with the lifespan protocol answered: started at once, and on shutdown the last two
    lines and the export handed over."""

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await app(scope, receive, send)
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                say_stopping(service=service)
                say_stopped(service=service)
                flush_telemetry()
                await send({"type": "lifespan.shutdown.complete"})
                return

    return application
