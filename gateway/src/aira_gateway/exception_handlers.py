"""The application's exception handlers: every error in the envelope of the surface it was aimed at.

KIRA paths answer in KIRA's envelope, `/v1beta` in Google's, everything else in AIRA's. Handled
here rather than in each route, because the errors that matter most — no credential, an oversized
body, a wrong URL, an unexpected bug — never reach a route that could name them, and a handler
cannot be forgotten by a route not written yet (`test_kira_envelope_everywhere.py` walks every
route on the surface).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aira_common.errors import ErrorDetail, ErrorResponse
from aira_common.logging import get_logger
from aira_common.observability import trace_context_fields
from aira_gateway.api.gemini.errors import GeminiHTTPError, gemini_error_response
from aira_gateway.api.kira.errors import VALIDATION_ERROR as KIRA_VALIDATION_ERROR
from aira_gateway.api.kira.errors import KiraError, kira_error_response
from aira_gateway.api.kira.errors import code_for_status as kira_code_for_status
from aira_gateway.api.kira.errors import (
    code_for_unauthenticated as kira_code_for_unauthenticated,
)
from aira_gateway.api.kira.headers import surface_headers, surface_refusal
from aira_gateway.api.serving.accounting import release_reservation
from aira_gateway.cors import apply_cors
from aira_gateway.middleware import (
    RequestTooLarge,
    SecurityHeadersMiddleware,
    TraceIdMiddleware,
    is_kira_path,
    too_large_response,
)

#: The Gemini surface's path prefix.
GEMINI_BASE = "/v1beta"

#: A wrong URL and a wrong method, named per surface by status rather than all called "not found".
#: KIRA's contract has a word for neither, so these name the case (`MIGRATION-KIRA.md`: extra codes
#: name cases the predecessor had no word for); Google's vocabulary has no 405, and
#: `INVALID_ARGUMENT` is its word for "fix the request".
_KIRA_ROUTING_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
_AIRA_ROUTING_CODES = {404: "not_found", 405: "method_not_allowed"}

_log = get_logger("aira_gateway")


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler in this module on ``app``."""
    app.exception_handler(RequestTooLarge)(_handle_too_large)
    app.exception_handler(GeminiHTTPError)(_handle_gemini_error)
    app.exception_handler(KiraError)(_handle_kira_error)
    app.exception_handler(StarletteHTTPException)(_handle_routing_error)
    app.exception_handler(RequestValidationError)(_handle_validation_error)
    app.exception_handler(Exception)(_handle_unexpected)


def _kira(request: Request) -> bool:
    """Whether this request was aimed at the KIRA compatibility surface."""
    return is_kira_path(request.url.path)


def _gemini(request: Request) -> bool:
    """Whether this request was aimed at the Gemini surface."""
    return request.url.path.startswith(GEMINI_BASE)


async def _handle_too_large(request: Request, exc: RequestTooLarge) -> JSONResponse:
    return too_large_response(request)


async def _handle_gemini_error(request: Request, exc: GeminiHTTPError) -> JSONResponse:
    """A shared refusal that reached the application rather than a route that could name it.

    KIRA routes render their own refusals (`KIRA_REFUSALS`), so what arrives here from that surface
    was raised by a dependency before the route ran — authentication, in practice.
    """
    if _kira(request):
        code = kira_code_for_status(exc.code)
        if exc.code == 401:
            # KIRA separates "nothing was presented" (a configuration slip) from "what was
            # presented was rejected" (a security signal); the dependency records which.
            code = kira_code_for_unauthenticated(
                bool(getattr(request.state, "credential_presented", False))
            )
        # `Retry-After` included: the bound on failed authentications is a 429 from here too.
        return surface_refusal(request, exc.code, code, exc.message, headers=exc.headers)
    return exc.to_response()


async def _handle_kira_error(request: Request, exc: KiraError) -> JSONResponse:
    """A refusal the KIRA surface named itself, raised from a route that did not catch it.

    Without this it leaves as a 500 in Google's envelope — for a permission check, a refusal
    reported as our own fault. Routes keep their own ``except``: they need it to close out the
    accounting, and a refusal already rendered never reaches here.
    """
    return surface_refusal(request, exc.status, exc.code, exc.message, exc.details)


async def _handle_routing_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """A wrong URL or method, in the surface's envelope rather than the framework's ``detail``.

    The framework's headers travel with it: a 405 names the methods that are allowed (`Allow`,
    RFC 9110 §15.5.6), which is the one thing a caller needs to fix the request.
    """
    detail = str(exc.detail) if exc.detail else "Not found."
    headers = dict(exc.headers or {})
    if _kira(request):
        code = _KIRA_ROUTING_CODES.get(exc.status_code) or kira_code_for_status(exc.status_code)
        return surface_refusal(request, exc.status_code, code, detail, headers=headers)
    if _gemini(request):
        status = "NOT_FOUND" if exc.status_code == 404 else "INVALID_ARGUMENT"
        return gemini_error_response(exc.status_code, detail, status, headers)
    code = _AIRA_ROUTING_CODES.get(exc.status_code, "invalid_request")
    envelope = ErrorResponse(error=ErrorDetail(code=code, message=detail))
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(exclude_none=True),
        headers=headers,
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A parameter the server will not accept, in the envelope of the surface it was sent to.

    **400, not 422**: `INVALID_ARGUMENT` is what the rest of the surface uses for "fix the
    request", and a client handed FastAPI's ``detail`` list reports "unknown error". The message
    names the parameter.
    """
    first = exc.errors()[0] if exc.errors() else {}
    location = [str(part) for part in first.get("loc", ()) if part not in ("query", "body")]
    field = ".".join(location) or "request"
    message = f"{field}: {first.get('msg', 'is not acceptable')}."
    if _kira(request):
        # The contract's own word for a value it will not take (`errors.VALIDATION_ERROR`).
        return surface_refusal(request, 400, KIRA_VALIDATION_ERROR, message)
    if _gemini(request):
        return gemini_error_response(400, message, "INVALID_ARGUMENT")
    envelope = ErrorResponse(error=ErrorDetail(code="invalid_request", message=message))
    return JSONResponse(status_code=400, content=envelope.model_dump(exclude_none=True))


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log full context server-side, return a safe, shaped error to the client.

    The one response no user middleware reaches: Starlette answers an unhandled exception from
    `ServerErrorMiddleware`, outside the whole stack. So what the middleware adds to every other
    response is added here, from its own definitions rather than second copies that could drift:
    the security headers, the trace id — which the log line below carries, and a failure is what
    somebody quotes it for (`FRD-117` FR-4) — the KIRA surface's headers, and the CORS headers.
    """
    # A bug between the reservation and the accounting block would otherwise hold a slice of the
    # use case's month until it expired; `accounting` records the row for the failures it sees.
    await release_reservation(request)
    attribution = getattr(request.state, "attribution", None)
    _log.error(
        "unhandled_error",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        error=str(exc),
        subject=getattr(attribution, "subject", None),
        use_case=getattr(attribution, "use_case", None),
        **trace_context_fields(),
    )
    if _kira(request):
        response = kira_error_response(500, kira_code_for_status(500), "Internal server error.")
        response.headers.update(surface_headers(request))
    elif _gemini(request):
        response = gemini_error_response(
            500, "Internal error while processing the request.", "INTERNAL"
        )
    else:
        envelope = ErrorResponse(
            error=ErrorDetail(code="internal_error", message="Internal server error.")
        )
        response = JSONResponse(status_code=500, content=envelope.model_dump(exclude_none=True))
    for name, value in SecurityHeadersMiddleware.HEADERS:
        response.headers.setdefault(name.decode("ascii"), value.decode("ascii"))
    trace_id = trace_context_fields().get("trace_id")
    if trace_id:
        response.headers.setdefault(TraceIdMiddleware.HEADER.decode("ascii"), trace_id)
    # And the cross-origin headers, or a browser reports a network error instead of this 500.
    await apply_cors(request, response)
    return response
