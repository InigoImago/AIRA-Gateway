"""ASGI middleware for the gateway.

- :class:`UseCasePathMiddleware` — the ``/uc/<use-case>`` path selector (`FRD-102`).
- :class:`BodySizeLimitMiddleware` — refuses oversized bodies before they are buffered (`ADR-0007`)
  and audits the refusal (:func:`record_oversized`).
- :class:`SecurityHeadersMiddleware` — the response headers a JSON API owes a browser.
- :class:`TraceIdMiddleware` — the trace id on every traced response (`FRD-117` FR-4).

All pure ASGI: `BaseHTTPMiddleware` runs the downstream app in a separate task, which loses the
OpenTelemetry span context, and a response from an exception handler must pass through too.
"""

from __future__ import annotations

import re

from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aira_common.logging import get_logger
from aira_common.observability import trace_context_fields
from aira_gateway.api.gemini.errors import gemini_error_response
from aira_gateway.api.kira import BASE as KIRA_BASE
from aira_gateway.api.kira.errors import code_for_status as kira_code_for_status
from aira_gateway.api.kira.headers import surface_refusal
from aira_gateway.audit import Outcome
from aira_gateway.auth.attribution import USE_CASE_PATH_KEY
from aira_gateway.db.base import new_id
from aira_gateway.explaining import (
    EXPLAINING_HEADERS,
    REASON_HEADER,
    REQUEST_ID_HEADER,
    TRAIL_STATE,
    answer_headers,
    explain,
    header_value,
)
from aira_gateway.persistence.recorder import client_ip
from aira_gateway.persistence.writer import PendingLog

#: A leading ``/uc/<slug>`` and the path after it.
_USE_CASE_PATH = re.compile(r"^/uc/([^/]+)(/.*)$")

#: `/v1beta/models/<model>:<method>` — the model may contain colons, the method never does.
_RESOURCE = re.compile(r"/models/(?P<resource>[^/]+)$")

#: The most `request_logs.request_bytes` holds (`INTEGER`). A declared length is the caller's own
#: number, and one past this would fail the very audit write it belongs to; a request that large is
#: measured as large either way.
_MAX_RECORDED_BYTES = 2**31 - 1


def is_kira_path(path: str) -> bool:
    """Whether ``path`` — with any `/uc/<slug>` prefix already stripped — is on the KIRA surface.

    One definition for every refusal answered before or outside a route: the body ceiling, the
    audit row it writes (`describe_target`) and the application's exception handlers.
    """
    return path.startswith(KIRA_BASE)


def too_large_message(limit: int) -> str:
    """The ceiling's refusal in words, naming the ceiling (`FRD-628` FR-6): a caller who is told
    only "too large" cannot tell by how much to shrink."""
    return f"Request body too large: the limit is {limit} bytes."


def too_large_response(request: Request, limit: int) -> JSONResponse:
    """The body ceiling's refusal, in the envelope of the surface it was aimed at.

    One response whichever exit refused the body — a declared length here, a body that outgrew it
    in the exception handler — because a KIRA client switches on `code`, and migrating
    (`FRD-107`) must be only a change of URL.
    """
    message = too_large_message(limit)
    explain(request.state, REASON_HEADER, str(Outcome.REQUEST_TOO_LARGE))
    if is_kira_path(request.url.path):
        return surface_refusal(request, 413, kira_code_for_status(413), message)
    return gemini_error_response(413, message, "INVALID_ARGUMENT")


class UseCasePathMiddleware:
    """Strip a leading ``/uc/<slug>`` so the normal routes match, and keep the slug in the scope.

    The ``X-AIRA-Use-Case`` header, resolved later, takes precedence over this path slug.
    """

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


def describe_target(path: str) -> tuple[str, str, str]:
    """``(api, model, operation)`` for a request refused before any route saw it.

    Best effort, read from the path; where it cannot tell it says ``unknown`` — an audit row naming
    the wrong model is worse than one admitting it does not know.
    """
    api = "kira" if is_kira_path(path) else "gemini"
    match = _RESOURCE.search(path)
    if not match:
        return api, "unknown", "unknown"
    model, separator, method = match.group("resource").rpartition(":")
    if not separator:
        return api, match.group("resource"), "unknown"
    return api, model, method


async def record_oversized(scope: Scope, limit: int) -> None:
    """Audit a request refused for its size (`FRD-122`: the log records what was **asked**).

    This refusal happens before any route, so the route's exception boundary never runs.

    - **Deliberately unattributed**: the credential has not been verified here, and recording it
      would let anyone write another identity into the audit trail with one oversized request.
    - **Where it came from, and how large**: the source address by the same trusted-proxy rule as
      every other row (`client_ip`), and the size it declared or was counted to, so the
      `payload_size` rule (`FRD-501`) measures the requests most worth measuring.
    - **Never fails the response**: a full writer queue must not turn a 413 into a 500, and this
      ceiling has to stay cheap under abuse.
    """
    app = scope.get("app")
    writer = getattr(getattr(app, "state", None), "log_writer", None)
    if writer is None:
        return

    api, model, operation = describe_target(scope.get("path", ""))
    measured = (scope.get("state") or {}).get("request_bytes")
    try:
        await writer.submit(
            PendingLog(
                row_id=(scope.get("state") or {}).get("request_id"),
                error=too_large_message(limit),
                subject="",
                auth_method="",
                use_case=None,
                source_ip=client_ip(Request(scope)),
                operation=operation,
                model=model,
                status=413,
                usage=None,
                latency_ms=None,
                trace_id=trace_context_fields().get("trace_id"),
                request_payload=None,
                # Never the body: storing what we refused to read would undo the refusal.
                response_payload=None,
                cost_nanos=None,
                outcome=str(Outcome.REQUEST_TOO_LARGE),
                requested_model=model,
                api=api,
                request_bytes=(
                    min(measured, _MAX_RECORDED_BYTES) if isinstance(measured, int) else None
                ),
            )
        )
    except Exception:  # noqa: BLE001 - an audit failure must not become a 500 (see above)
        get_logger("aira_gateway").warning("oversized_request_not_audited", limit=limit)


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes``.

    A declared ``Content-Length`` is rejected up front; bodies streamed without one are counted as
    they arrive and abort as soon as the limit is passed, so an unbounded upload is never buffered.
    Both exits record the refusal through :func:`record_oversized`, and both answer with
    :func:`too_large_response`.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = self._declared_length(scope)
        if declared > self.max_bytes:
            # What it asked to send is the size an oversized request is measured by (`FRD-501`).
            scope.setdefault("state", {})["request_bytes"] = declared
            await record_oversized(scope, self.max_bytes)
            await self._reject(scope, send)
            return

        received = 0
        # ASGI `state` surfaces as `request.state`; the count is what `FRD-501`'s `payload_size`
        # rule measures.
        state = scope.setdefault("state", {})

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                state["request_bytes"] = received
                if received > self.max_bytes:
                    # A body that lied about its length, or declared none. Recorded here rather
                    # than in the exception handler, whose Request has no resolved attribution.
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

    async def _reject(self, scope: Scope, send: Send) -> None:
        response = too_large_response(Request(scope), self.max_bytes)
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": response.raw_headers,
            }
        )
        await send({"type": "http.response.body", "body": response.body})


class SecurityHeadersMiddleware:
    """The response headers a JSON API owes a browser (`ADR-0007`).

    The console calls this service from a browser through the `/gw` proxy, so:

    - `X-Content-Type-Options: nosniff` — a JSON body a browser sniffs as HTML is the ingredient of
      every reflected-content trick.
    - `Referrer-Policy: no-referrer` — Gemini clients send `?key=<api key>`; a followed link would
      leak it in `Referer`.
    - `Cache-Control: no-store` — responses carry other people's prompts and spend. On **every**
      response, health probes included: a cached probe is a liveness answer about the past.
    - `X-Frame-Options: DENY` — nothing here is meant to be embedded.

    Defaults, not overrides: a route that set one of these keeps its own value. **No HSTS and no
    CSP**: TLS terminates in front of this service, and a content policy on a JSON API constrains
    nothing while reading as a protection.
    """

    HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"cache-control", b"no-store"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in self.HEADERS if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestIdMiddleware:
    """Give every request an id, and put it — with whatever explains the answer — on the response.

    The id is drawn here, before any route runs, so a refusal from a dependency, the body ceiling
    or an exception handler carries it as an answer does; the audit row takes the same id
    (`persistence.recorder.claim_request_id`). Headers rather than body fields, because a Gemini
    SDK validates the body it parses and ignores a header it does not know (`FRD-628` §2).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        request_id = state.setdefault("request_id", new_id())

        async def send_explained(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                trail = state.get(TRAIL_STATE)
                # What the trail says — the model that answered, what the pipeline did — then what
                # a route said itself (`X-AIRA-Reason`), which is the more specific of the two.
                answered = answer_headers(trail, message["status"]) if trail is not None else {}
                added = {
                    REQUEST_ID_HEADER: str(request_id),
                    **answered,
                    **(state.get(EXPLAINING_HEADERS) or {}),
                }
                headers.extend(
                    (name.encode("latin-1"), header_value(value))
                    for name, value in added.items()
                    if name.encode("latin-1") not in present
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_explained)


class TraceIdMiddleware:
    """Put the active trace id on every **traced** response, failures included (`FRD-117` FR-4).

    Health probes are excluded from tracing (`observability.HEALTH_PATHS`), so they carry no
    header. Mounted outermost of the user middleware, because the requests that most need
    correlating are the ones an exception handler answered. Two layers sit outside it — the OTel
    middleware, and Starlette's `ServerErrorMiddleware`, which answers an unhandled error — so that
    500 gets its id from `_handle_unexpected` instead. Absent when no span is active: an id that
    correlates with nothing is worse than none, because somebody will search for it.
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
