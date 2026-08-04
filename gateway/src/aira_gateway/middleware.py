"""ASGI middleware for the ``/uc/<use-case>`` path selector (FRD-102).

Strips a leading ``/uc/<slug>`` from the request path and stashes the slug in the scope so
the normal Gemini routes still match. The header ``X-AIRA-Use-Case`` (resolved later) takes
precedence over this path slug.
"""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Receive, Scope, Send

from aira_gateway.auth.attribution import USE_CASE_PATH_KEY

_USE_CASE_PATH = re.compile(r"^/uc/([^/]+)(/.*)$")


class UseCasePathMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            match = _USE_CASE_PATH.match(scope.get("path", ""))
            if match:
                scope = dict(scope)
                scope[USE_CASE_PATH_KEY] = match.group(1)
                scope["path"] = match.group(2)
                scope["raw_path"] = match.group(2).encode("utf-8")
        await self.app(scope, receive, send)
