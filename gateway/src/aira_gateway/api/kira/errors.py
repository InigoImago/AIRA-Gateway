"""KIRA's error envelope and vocabulary.

A different envelope and different codes from Gemini's, both kept faithfully: a migrating client
changes a base URL and nothing else, so its error handling has to keep matching.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import ValidationError

# The contract's codes this surface emits. A code nothing raises is not declared here.
NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
INVALID_TOKEN = "INVALID_TOKEN"
ADMIN_PERMISSION_REQUIRED = "ADMIN_PERMISSION_REQUIRED"
STANDARD_USER_PERMISSION_REQUIRED = "STANDARD_USER_PERMISSION_REQUIRED"
MISSING_QUERY_PARAM = "MISSING_QUERY_PARAM"
INVALID_TIME_RANGE = "INVALID_TIME_RANGE"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
NO_CHAT_CAPABILITIES = "NO_CHAT_CAPABILITIES"
NO_EMBEDDING_CAPABILITIES = "NO_EMBEDDING_CAPABILITIES"
INVALID_MAX_TOKENS = "INVALID_MAX_TOKENS"
MAX_TOKENS_EXCEEDS_CAP = "MAX_TOKENS_EXCEEDS_CAP"
VALIDATION_ERROR = "VALIDATION_ERROR"
EXTERNAL_KI_API_TOO_MANY_REQUEST = "EXTERNAL_KI_API_TOO_MANY_REQUEST"
EXTERNAL_KI_API_ERROR = "EXTERNAL_KI_API_ERROR"
INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"

#: Not a KIRA code: the contract has none for "this gateway does not do that yet", because the
#: predecessor always did (`FRD-107` FR-2a).
NOT_YET_SUPPORTED = "NOT_YET_SUPPORTED"

#: A refusal raised *before* a KIRA route ran — by a dependency or middleware — in this surface's
#: vocabulary. Only the statuses that can arrive that way; a route catches and renders its own.
STATUS_CODES: dict[int, str] = {
    401: NOT_AUTHENTICATED,
    403: STANDARD_USER_PERMISSION_REQUIRED,
    # The body ceiling, refused in ASGI before any route (`middleware.py`).
    413: VALIDATION_ERROR,
    # The bound on failed authentications (`ADR-0015`).
    429: EXTERNAL_KI_API_TOO_MANY_REQUEST,
}


class KiraError(Exception):
    """Raised to return a KIRA-shaped error."""

    def __init__(
        self, status: int, code: str, message: str, details: list[Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_response(self) -> JSONResponse:
        return kira_error_response(self.status, self.code, self.message, self.details)


def kira_error_response(
    status: int, code: str, message: str, details: list[Any] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status, content=body)


def code_for_status(status: int) -> str:
    """This surface's code for a status raised before any route saw the request.

    Unmapped is `INTERNAL_SERVER_ERROR`: a more specific guess would tell the caller to fix
    something that is not theirs.
    """
    return STATUS_CODES.get(status, INTERNAL_SERVER_ERROR)


def code_for_unauthenticated(credential_presented: bool) -> str:
    """`INVALID_TOKEN` for a credential offered and rejected, `NOT_AUTHENTICATED` for none at all.

    They mean different things to whoever is on call: a client that forgot its key is a deployment
    slip; a rejected key may be a missed rotation, a revoked credential, or an attempt worth a look.
    """
    return INVALID_TOKEN if credential_presented else NOT_AUTHENTICATED


def validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    """The predecessor's ``details`` array: location and message, nothing else.

    `errors()` can carry the raising exception in ``ctx``, which does not serialise (a custom
    validator's refusal became a 500), and echoing ``input`` back can reflect a prompt or a
    misplaced credential into a response. The comprehension copies two named fields; the flags are
    belt and braces.
    """
    return [
        {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
