"""One line per HTTP request, as both planes write it (`FRD-631` FR-5).

The web server's own access line was text on stdout, outside the export, with the caller's address
in plain text and the query string in it — `?q=<a person's name>` on a directory search, a
credential under `?key=` before it was redacted — and without the one figure an operator reads an
access log for: how long the request took. So the server's is switched off
(`aira_common.logging.foreign`) and this one is written instead, by each plane, through the same
pipeline as every other line:

- the method, the **route** the request matched with its parameters as names
  (``/api/v1/use-cases/{slug}/members/{username}/``, never the name in it), and the path only where
  no route matched — without the query string, always;
- the status and the duration, the address as the installation writes addresses, and whatever the
  plane adds: the request's id, who made it;
- **not** for a health probe, which asks every fifteen seconds and says nothing about anybody.
"""

from __future__ import annotations

import re
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

#: Where a regular expression names a parameter: ``(?P<slug>`` up to the group's own end.
_NAMED_GROUP = "(?P<"
#: A Django ``path()`` parameter, ``<str:username>`` or ``<username>``.
_CONVERTER = re.compile(r"<(?:\w+:)?(?P<name>\w+)>")
#: A Starlette parameter with its converter, ``{model:path}``.
_TYPED_PARAMETER = re.compile(r"\{(?P<name>\w+):[^}]+\}")
#: A character a regular expression escapes to mean itself: ``\.`` is a dot.
_ESCAPED_LITERAL = re.compile(r"\\(?P<char>[^0-9A-Za-z])")


def _group_end(pattern: str, start: int) -> int:
    """Where the group opened just before ``start`` ends: the index after its ``)``.

    A ``)`` inside a character class or behind a backslash closes nothing.
    """
    depth = 1
    index = start
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index += 1
            # A `]` first in the class, or after its `^`, is a member and ends nothing.
            if index < len(pattern) and pattern[index] == "^":
                index += 1
            if index < len(pattern) and pattern[index] == "]":
                index += 1
            while index < len(pattern) and pattern[index] != "]":
                index += 2 if pattern[index] == "\\" else 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(pattern)


def _named_groups_as_names(pattern: str) -> str:
    """Every ``(?P<name>…)`` of ``pattern`` as ``{name}``, whatever the group matches."""
    written: list[str] = []
    index = 0
    while (found := pattern.find(_NAMED_GROUP, index)) != -1:
        name_end = pattern.find(">", found)
        if name_end == -1:
            break
        written.append(pattern[index:found])
        written.append("{" + pattern[found + len(_NAMED_GROUP) : name_end] + "}")
        index = _group_end(pattern, name_end + 1)
    written.append(pattern[index:])
    return "".join(written)


def readable_route(route: str | None) -> str | None:
    """``route`` in the one spelling every line uses: parameters as ``{name}``, a leading ``/``.

    The gateway's framework writes a route as ``/v1beta/usage/{use_case}``. Django writes a
    router's as the regular expression it matches — ``api/v1/use-cases/(?P<slug>[^/.]+)/groups/$``
    — and a ``path()`` as ``api/v1/directory/search`` or ``…/members/<str:username>/``. A reader
    filtering a log store by route would need a spelling per plane, and an escape for every bracket,
    so both are written as ``/api/v1/use-cases/{slug}/groups/``. The name is the parameter's,
    never its value: a slug or a username stays out of the line as before. What this cannot
    spell — an unnamed group, a class outside one — stays as it is, and the test over every
    registered pattern fails on it.
    """
    if not route:
        return None
    written = _named_groups_as_names(route)
    written = _CONVERTER.sub(lambda found: "{" + found["name"] + "}", written)
    written = _TYPED_PARAMETER.sub(lambda found: "{" + found["name"] + "}", written)
    written = written.removeprefix("^").removesuffix("$")
    # An optional slash — a router's format suffix, `\.(?P<format>…)/?$` — is the route without it.
    written = written.replace("/?", "")
    written = _ESCAPED_LITERAL.sub(lambda found: found["char"], written)
    return written if written.startswith("/") else "/" + written


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
    return readable_route(path) if isinstance(path, str) else None


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
