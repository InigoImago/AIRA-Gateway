"""One line per HTTP request, as both planes write it (`FRD-631` FR-5).

The web server's own access line was text on stdout, outside the export, with the caller's address
in plain text and the query string in it — `?q=<a person's name>` on a directory search, a
credential under `?key=` before it was redacted — and without the one figure an operator reads an
access log for: how long the request took. So the server's is switched off
(`aira_common.logging.foreign`) and this one is written instead, by each plane, through the same
pipeline as every other line:

- the method, the **route** the request matched with its parameters as names
  (``/api/v1/use-cases/<slug>/members/<username>/``, never the name in it), and the path only where
  no route matched — without the query string, always;
- the status and the duration, the address as the installation writes addresses, and whatever the
  plane adds: the request's id, who made it;
- **not** for a health probe, which asks every fifteen seconds and says nothing about anybody.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from aira_common.logging.pipeline import get_logger

# The ASGI shapes, spelled here rather than imported from a framework: both planes use this module,
# and only one of them runs Starlette.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_log = get_logger("aira.access")

#: The event every access line carries.
EVENT = "http_request"

#: The paths a health probe asks — the image's healthcheck every fifteen seconds, an orchestrator's
#: readiness probe — and that therefore write no line.
PROBES = frozenset({"/healthz", "/readyz"})

#: How much of an unmatched path a line keeps: a scanner's paths are long and say nothing more.
PATH_CHARACTERS = 200


def write_access_line(
    *,
    method: str,
    status: int,
    duration_ms: float,
    route: str | None,
    path: str,
    **fields: Any,
) -> None:
    """The access line itself, for either plane: the route where one matched, else the path."""
    where: dict[str, str] = {"route": route} if route else {"path": path[:PATH_CHARACTERS]}
    _log.info(
        EVENT,
        method=method,
        status=status,
        duration_ms=round(duration_ms, 1),
        **where,
        **{key: value for key, value in fields.items() if value is not None},
    )


def route_of(scope: Mapping[str, Any]) -> str | None:
    """The template of the route a Starlette or FastAPI application matched, if it matched one."""
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    return path if isinstance(path, str) else None


class AccessLog:
    """ASGI middleware: one access line when a request has been answered — or failed to be.

    Pure ASGI, so the span of the request is still the current one and the line carries its trace
    id. ``describe`` adds what only the plane knows, read from the scope once the application is
    done with it.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        describe: Callable[[Scope], Mapping[str, Any]] | None = None,
        probes: frozenset[str] = PROBES,
    ) -> None:
        self.app = app
        self._describe = describe
        self._probes = probes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._probes:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        # Until the application says otherwise, what the server answers an exception with.
        status = 500

        async def answering(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, answering)
        finally:
            extra: Mapping[str, Any] = {}
            if self._describe is not None:
                try:
                    extra = self._describe(scope)
                except Exception:  # noqa: BLE001 — a detail must never cost the line
                    extra = {}
            write_access_line(
                method=str(scope.get("method", "")),
                status=status,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                route=route_of(scope),
                path=str(scope.get("path", "")),
                **extra,
            )
