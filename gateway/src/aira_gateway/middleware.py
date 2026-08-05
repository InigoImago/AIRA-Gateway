"""ASGI middleware for the gateway.

- :class:`UseCasePathMiddleware` — the ``/uc/<use-case>`` path selector (FRD-102). Strips a
  leading ``/uc/<slug>`` from the request path and stashes the slug in the scope so the normal
  Gemini routes still match. The header ``X-AIRA-Use-Case`` (resolved later) takes precedence
  over this path slug.
- :class:`BodySizeLimitMiddleware` — refuses oversized request bodies before they are buffered
  into memory (ADR-0007).
"""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class RequestTooLarge(Exception):
    """Raised when a request body exceeds the configured ceiling."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Request body exceeds the {limit} byte limit.")
        self.limit = limit


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes``.

    A declared ``Content-Length`` is rejected up front; bodies streamed without one are counted
    as they arrive and abort the request as soon as the limit is passed, so an unbounded upload
    can never be buffered into memory.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if self._declared_length(scope) > self.max_bytes:
            await self._reject(send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLarge(self.max_bytes)
            return message

        await self.app(scope, counting_receive, send)

    def _declared_length(self, scope: Scope) -> int:
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0

    async def _reject(self, send: Send) -> None:
        body = (
            b'{"error":{"code":413,"message":"Request body too large.",'
            b'"status":"INVALID_ARGUMENT"}}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
