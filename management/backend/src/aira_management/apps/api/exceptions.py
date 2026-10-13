"""DRF exception handler producing a consistent error envelope (FRD-200).

Shape mirrors the gateway: ``{"error": {"code", "message", "details"}}`` — for every failure,
including the ones nobody anticipated. An unexpected exception answers ``500`` with
``internal_error`` in the same envelope rather than Django's HTML page, which the console's
`errorMessage` cannot read; the exception itself is logged here, where its context still exists.
"""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from aira_common.logging import get_logger
from aira_common.observability import trace_context_fields

#: The public code of each status this API answers with on purpose — the gateway's names, so a
#: client reading both planes reads one vocabulary. A status not listed takes the exception's own
#: `default_code` (`directory_unavailable`, …), which says more than a generic word.
_STATUS_CODES = {
    400: "invalid_argument",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
    500: "internal_error",
}

#: What a caller is told about a failure that is this service's, not theirs. Nothing from the
#: exception: its text can name a column, a value or a path, and it is logged instead.
INTERNAL_ERROR_MESSAGE = "Internal server error."

_log = get_logger("aira_management")


def envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """The error body every refusal of this plane carries, built in one place."""
    return {"error": {"code": code, "message": message, "details": details}}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    response = drf_exception_handler(exc, context)
    if response is None:
        return _unexpected(exc, context)

    data = response.data
    if isinstance(data, dict) and "detail" in data:
        message = _text(data["detail"])
        details = None
    else:
        message = "Request failed."
        details = data

    default = exc.default_code if isinstance(exc, APIException) else "error"
    response.data = envelope(
        _STATUS_CODES.get(response.status_code, str(default)), message, details
    )
    return response


def _text(detail: Any) -> str:
    """A `detail` as the sentence it holds. A view that raised `{"detail": [sentence]}` handed
    over a list, and `str()` of a list is its repr — `[ErrorDetail(string='…')]` — not a message."""
    if isinstance(detail, list | tuple):
        return " ".join(str(item) for item in detail)
    return str(detail)


def _unexpected(exc: Exception, context: dict[str, Any]) -> Response:
    """The last resort: the full context to the log, a shaped `500` to the caller.

    Rolled back as DRF rolls back the failures it handles: answering instead of raising must not
    let an enclosing transaction commit half of what the view did.
    """
    set_rollback()
    request = context.get("request")
    _log.error(
        "unhandled_error",
        path=getattr(request, "path", None),
        method=getattr(request, "method", None),
        error_type=type(exc).__name__,
        error=str(exc),
        exc_info=exc,
        **trace_context_fields(),
    )
    return Response(envelope(_STATUS_CODES[500], INTERNAL_ERROR_MESSAGE), status=500)
