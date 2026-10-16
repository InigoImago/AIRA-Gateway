"""Gemini-shaped error responses, shared by routes and the auth layer."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from aira_gateway.api.gemini import schemas

#: Google's type name for the detail that says which control refused, and the domain this
#: gateway's reasons belong to (`google.rpc.ErrorInfo`). A Gemini SDK reads both from `details`.
ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"
ERROR_DOMAIN = "aira.gateway"


def error_info(reason: str, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    """One `google.rpc.ErrorInfo`: the reason in Google's upper snake case, and its metadata."""
    return {
        "@type": ERROR_INFO_TYPE,
        "reason": reason.upper(),
        "domain": ERROR_DOMAIN,
        "metadata": dict(metadata or {}),
    }


def gemini_error_response(
    code: int,
    message: str,
    status: str,
    headers: dict[str, str] | None = None,
    *,
    reason: str | None = None,
    metadata: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a Gemini error-envelope JSON response.

    ``headers`` carries the response metadata a client needs to act on the refusal — today only
    ``Retry-After`` on a rate-limit rejection, without which a well-behaved client has no choice
    but to retry immediately against the thing the limit protects. ``reason`` adds Google's
    `ErrorInfo` to `details` (`FRD-628` FR-3); the status and the message stay what they were.
    """
    detail = schemas.GeminiErrorDetail(
        code=code,
        message=message,
        status=status,
        details=[error_info(reason, metadata)] if reason else None,
    )
    body = schemas.GeminiError(error=detail)
    return JSONResponse(
        status_code=code, content=body.model_dump(exclude_none=True), headers=headers
    )


class GeminiHTTPError(Exception):
    """Raised to return a Gemini-shaped error (e.g. from an auth dependency)."""

    def __init__(
        self, code: int, message: str, status: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        #: Response metadata the caller needs to act on the refusal. A 429 raised from an auth
        #: dependency has to be able to carry `Retry-After`, or the client's only option is the
        #: immediate retry the bound exists to stop.
        self.headers = headers

    def to_response(self) -> JSONResponse:
        return gemini_error_response(self.code, self.message, self.status, self.headers)
