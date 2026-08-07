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

from aira_common.logging import get_logger
from aira_common.observability import trace_context_fields
from aira_gateway.audit import Outcome
from aira_gateway.auth.attribution import USE_CASE_PATH_KEY
from aira_gateway.persistence.writer import PendingLog

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


#: `/v1beta/models/<model>:<method>` — the model may contain colons, the method never does.
_RESOURCE = re.compile(r"/models/(?P<resource>[^/]+)$")


def describe_target(path: str) -> tuple[str, str, str]:
    """``(api, model, operation)`` for a request refused before any route saw it.

    Best effort by design. A request rejected on its declared size never reaches the routing
    table, so this reads the path — and where it cannot tell, it says ``unknown`` rather than
    guessing. An audit row that names the wrong model is worse than one that admits it does not
    know which was meant.
    """
    api = "kira" if path.startswith("/kira/") else "gemini"
    match = _RESOURCE.search(path)
    if not match:
        return api, "unknown", "unknown"
    model, separator, method = match.group("resource").rpartition(":")
    if not separator:
        return api, match.group("resource"), "unknown"
    return api, model, method


async def record_oversized(scope: Scope, limit: int) -> None:
    """Audit a request refused for its size (`FRD-122`, extended).

    `FRD-122`'s rule is that the log records what was **asked**, not only what was served, and it
    closed that gap at the route's exception boundary — one site, because a fact repeated at every
    exit is a fact eventually forgotten at one of them. This refusal happens *before* any route,
    in pure ASGI, so the boundary never runs and the request left no trace at all. Found by
    posting a 20 MB body at a running gateway and counting rows.

    **Deliberately unattributed.** The credential in the header has not been verified at this
    point, and recording it would let anyone write another system's identity into the audit trail
    by sending one oversized request. An unverifiable claim is not evidence; the source IP and the
    outcome are. This is the same reasoning as "unpriced is not free" and "undeclared is not
    permitted", applied to identity.

    Never allowed to fail the response. A full writer queue turning a 413 into a 500 is the exact
    defect `FRD-405`'s review found on the refusal path, and it would be worse here — the whole
    point of this ceiling is to stay cheap under abuse.
    """
    app = scope.get("app")
    writer = getattr(getattr(app, "state", None), "log_writer", None)
    if writer is None:
        return

    api, model, operation = describe_target(scope.get("path", ""))
    client = scope.get("client")
    try:
        await writer.submit(
            PendingLog(
                subject="",
                auth_method="",
                use_case=None,
                source_ip=client[0] if client else None,
                operation=operation,
                model=model,
                status=413,
                usage=None,
                latency_ms=None,
                trace_id=trace_context_fields().get("trace_id"),
                request_payload=None,
                # Never the body: it is over the ceiling, and storing what we refused to read
                # would undo the reason for refusing it.
                response_payload=None,
                cost_nanos=None,
                outcome=str(Outcome.REQUEST_TOO_LARGE),
                requested_model=model,
                api=api,
            )
        )
    except Exception:  # noqa: BLE001 - an audit failure must not become a 500 (see above)
        get_logger("aira_gateway").warning("oversized_request_not_audited", limit=limit)


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes``.

    A declared ``Content-Length`` is rejected up front; bodies streamed without one are counted
    as they arrive and abort the request as soon as the limit is passed, so an unbounded upload
    can never be buffered into memory.

    Both paths record the refusal (:func:`record_oversized`) — through one function, because the
    two ways of being too large are two exits from one decision.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if self._declared_length(scope) > self.max_bytes:
            await record_oversized(scope, self.max_bytes)
            await self._reject(send)
            return

        received = 0
        # ASGI `state` is the standard channel to the application, and Starlette surfaces it as
        # `request.state`. The count is already being taken to enforce the ceiling; recording it
        # is what lets `FRD-501`'s `payload_size` rule measure anything at all.
        state = scope.setdefault("state", {})

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                state["request_bytes"] = received
                if received > self.max_bytes:
                    # The other exit: a body that lied about its length, or declared none. Same
                    # refusal, same row — recorded here rather than in the exception handler,
                    # because that handler has a Request whose attribution was never resolved and
                    # would look like it knew who this was.
                    await record_oversized(scope, self.max_bytes)
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


class TraceIdMiddleware:
    """Put the active trace id on **every** response, including the failures (`FRD-117` FR-4).

    Pure ASGI and mounted outermost, and both are load-bearing. Outermost, because a response
    produced by an exception handler is still a response — and the requests that most need
    correlating are exactly the ones that went wrong. `BaseHTTPMiddleware` is avoided because it
    runs the downstream app in a separate task, which loses the OpenTelemetry span context: the
    header would then be absent precisely when a span exists.

    Absent when no span is active, rather than a placeholder. An id that correlates with nothing is
    worse than no id, because somebody will search for it.
    """

    HEADER = b"x-trace-id"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                trace_id = trace_context_fields().get("trace_id")
                if trace_id:
                    headers = list(message.get("headers") or [])
                    headers.append((self.HEADER, str(trace_id).encode("ascii")))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace)
