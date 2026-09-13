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

from aira_common.errors import AiraError, ErrorDetail, ErrorResponse
from aira_common.logging import get_logger
from aira_common.observability import trace_context_fields
from aira_gateway.api.gemini.errors import GeminiHTTPError, gemini_error_response
from aira_gateway.api.kira import BASE as KIRA_BASE
from aira_gateway.api.kira.errors import KiraError, kira_error_response
from aira_gateway.api.kira.errors import code_for_status as kira_code_for_status
from aira_gateway.api.kira.errors import (
    code_for_unauthenticated as kira_code_for_unauthenticated,
)
from aira_gateway.middleware import RequestTooLarge, SecurityHeadersMiddleware

#: The Gemini surface's path prefix.
GEMINI_BASE = "/v1beta"

_log = get_logger("aira_gateway")


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler in this module on ``app``."""
    app.exception_handler(RequestTooLarge)(_handle_too_large)
    app.exception_handler(AiraError)(_handle_aira_error)
    app.exception_handler(GeminiHTTPError)(_handle_gemini_error)
    app.exception_handler(KiraError)(_handle_kira_error)
    app.exception_handler(StarletteHTTPException)(_handle_routing_error)
    app.exception_handler(RequestValidationError)(_handle_validation_error)
    app.exception_handler(Exception)(_handle_unexpected)


def _kira(request: Request) -> bool:
    """Whether this request was aimed at the KIRA compatibility surface."""
    return request.url.path.startswith(KIRA_BASE)


def _gemini(request: Request) -> bool:
    """Whether this request was aimed at the Gemini surface."""
    return request.url.path.startswith(GEMINI_BASE)


async def _handle_too_large(request: Request, exc: RequestTooLarge) -> JSONResponse:
    message = "Request body too large."
    if _kira(request):
        return kira_error_response(413, kira_code_for_status(413), message)
    return gemini_error_response(413, message, "INVALID_ARGUMENT")


async def _handle_aira_error(_request: Request, exc: AiraError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(exclude_none=True),
    )


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
        return kira_error_response(exc.code, code, exc.message)
    return exc.to_response()


async def _handle_kira_error(_request: Request, exc: KiraError) -> JSONResponse:
    """A refusal the KIRA surface named itself, raised from a route that did not catch it.

    Without this it leaves as a 500 in Google's envelope — for a permission check, a refusal
    reported as our own fault. Routes keep their own ``except``: they need it to close out the
    accounting, and a refusal already rendered never reaches here.
    """
    return kira_error_response(exc.status, exc.code, exc.message, exc.details)


async def _handle_routing_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """A wrong URL or method, in the surface's envelope rather than the framework's ``detail``."""
    detail = str(exc.detail) if exc.detail else "Not found."
    if _kira(request):
        return kira_error_response(exc.status_code, "NOT_FOUND", detail)
    if _gemini(request):
        status = "NOT_FOUND" if exc.status_code == 404 else "INVALID_ARGUMENT"
        return gemini_error_response(exc.status_code, detail, status)
    envelope = ErrorResponse(error=ErrorDetail(code="not_found", message=detail))
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())


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
        return kira_error_response(400, "INVALID_REQUEST", message)
    if _gemini(request):
        return gemini_error_response(400, message, "INVALID_ARGUMENT")
    envelope = ErrorResponse(error=ErrorDetail(code="invalid_request", message=message))
    return JSONResponse(status_code=400, content=envelope.model_dump())


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log full context server-side, return a safe, shaped error to the client.

    The one response `SecurityHeadersMiddleware` cannot reach: Starlette answers an unhandled
    exception from `ServerErrorMiddleware`, outside the whole user middleware stack. So the headers
    are applied here, from that middleware's own tuple rather than a second copy that could drift.
    """
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
    return response
