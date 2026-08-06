"""KIRA's error vocabulary (`kira_api.md` §6).

A different envelope from Gemini's and a different set of codes. Both are kept faithfully, because
a compatibility surface whose errors a client cannot match is not compatible — the whole point is
that a consumer changes a base URL and nothing else.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


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


# The codes from `kira_api.md` §6.2 that Stage A can produce. The ones belonging to features that
# do not exist yet (thinking bounds, embedding task types) arrive with those features rather than
# being declared here as constants nothing raises.
NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
INVALID_TOKEN = "INVALID_TOKEN"
ADMIN_PERMISSION_REQUIRED = "ADMIN_PERMISSION_REQUIRED"
STANDARD_USER_PERMISSION_REQUIRED = "STANDARD_USER_PERMISSION_REQUIRED"
INVALID_JSON_BODY = "INVALID_JSON_BODY"
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

#: Stage A refuses what it cannot yet honour, in the predecessor's own vocabulary rather than in
#: ours (`FRD-107` FR-2a). Not a KIRA code — the predecessor has none for "this gateway does not
#: do that yet", because it always did. A new code is the honest answer to a new situation.
NOT_YET_SUPPORTED = "NOT_YET_SUPPORTED"
