"""One access line per request to Management's API (`FRD-631` FR-5).

The plane's half of `aira_common.logging.access`: the route Django matched — the pattern, so
`…/members/{username}/` is never the name in it — the status, the duration, who made it where the
token named somebody, and the address as the installation writes one. Outermost but for the trace's
own middleware, so the line carries the request's trace id; after the view, so the caller DRF
authenticated is known. A health probe writes none.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponseBase

from aira_common.forwarded import trusted_proxies
from aira_common.logging.access import PROBES, readable_route, write_access_line
from aira_management.config.runtime import get_settings

#: The request being answered, for what happens inside it and needs to know who asked — the
#: configuration history's actor (`FRD-632`). Set for exactly the length of one request.
_current: ContextVar[HttpRequest | None] = ContextVar("aira_current_request", default=None)


def current_request() -> HttpRequest | None:
    """The request this code runs inside, or ``None`` outside one — a command, a migration."""
    return _current.get()


def source_of(request: HttpRequest) -> str | None:
    """The caller's address, read as the gateway reads it (`FRD-156`): through the proxies
    `AIRA_TRUSTED_PROXIES` names, from the right of `X-Forwarded-For`; the connection's own address
    where none is named. What the access line and a refusal's line name, and what the bound on
    failed sign-ins counts against."""
    try:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        return trusted_proxies(get_settings().trusted_proxies).client(
            request.META.get("REMOTE_ADDR"), [forwarded] if forwarded else []
        )
    except Exception:  # noqa: BLE001 — an address that cannot be read is not the request's failure
        return None


def route_of(request: HttpRequest | None) -> str | None:
    """The route Django matched, as every line writes one: ``/api/v1/use-cases/{slug}/groups/``.

    The pattern and never the path, which names whom it is about (``…/members/<username>/``). The
    one place this plane reads a route for a line, so a line cannot carry the router's regular
    expression by reading it itself.
    """
    match = getattr(request, "resolver_match", None)
    return readable_route(getattr(match, "route", None))


def _actor(request: HttpRequest) -> str | None:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return str(user.get_username())


def write_line(request: HttpRequest, response: HttpResponseBase | None, started: float) -> None:
    if request.path in PROBES:
        return
    write_access_line(
        method=request.method or "",
        status=response.status_code if response is not None else 500,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        route=route_of(request),
        path=request.path,
        actor=_actor(request),
        source_ip=source_of(request),
    )


class AccessLine:
    """Write the access line once the response exists — or once the request failed."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: Callable[[HttpRequest], Any]) -> None:
        self.get_response = get_response
        self._async = iscoroutinefunction(get_response)
        if self._async:
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> HttpResponseBase | Awaitable[HttpResponseBase]:
        if self._async:
            return self.__acall__(request)
        started = time.perf_counter()
        response: HttpResponseBase | None = None
        token = _current.set(request)
        try:
            response = self.get_response(request)
            return response
        finally:
            _current.reset(token)
            write_line(request, response, started)

    async def __acall__(self, request: HttpRequest) -> HttpResponseBase:
        started = time.perf_counter()
        response: HttpResponseBase | None = None
        # The view runs in a thread of its own, which is handed a copy of this context.
        token = _current.set(request)
        try:
            response = await self.get_response(request)
            return response
        finally:
            _current.reset(token)
            write_line(request, response, started)
