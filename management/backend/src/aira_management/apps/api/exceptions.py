"""DRF exception handler producing a consistent error envelope (FRD-200).

Shape mirrors the gateway: ``{"error": {"code", "message", "details"}}`` — for every failure,
including the ones nobody anticipated. An unexpected exception answers ``500`` with
``internal_error`` in the same envelope rather than Django's HTML page, which the console's
`errorMessage` cannot read; the exception itself is logged here, where its context still exists.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from aira_common.logging import get_logger
from aira_common.logging.refusals import UNAUTHENTICATED, say_refused
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

#: The outcome of a refusal for a caller who is known and may not do what was asked.
FORBIDDEN = "forbidden"


class BodyTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "The request body is larger than this service accepts."
    default_code = "payload_too_large"


def envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """The error body every refusal of this plane carries, built in one place."""
    return {"error": {"code": code, "message": message, "details": details}}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    if isinstance(exc, RequestDataTooBig):
        # **A body over the ceiling is the caller's, and a 413.** DRF reads every JSON body through
        # `request.body`, where Django applies `DATA_UPLOAD_MAX_MEMORY_SIZE` before reading it and
        # raises an exception DRF does not translate — which would otherwise answer 500.
        exc = BodyTooLarge(
            f"The request body is larger than {settings.DATA_UPLOAD_MAX_MEMORY_SIZE} bytes."
        )
    response = drf_exception_handler(exc, context)
    if response is None:
        return _unexpected(exc, context)
    _say_if_refused(exc, context)

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


def _say_if_refused(exc: Exception, context: dict[str, Any]) -> None:
    """A refused sign-in or a refused permission is a line (`FRD-631` FR-6), bounded per source.

    A `429` is one only for a caller nobody authenticated — the bound on failed sign-ins; an
    authenticated caller's `429` is the ordinary per-user rate. The reason is DRF's own sentence,
    never what the caller sent.
    """
    request = context.get("request")
    if request is None:
        return
    user = getattr(request, "user", None)
    known = bool(getattr(user, "is_authenticated", False))
    if isinstance(exc, NotAuthenticated | AuthenticationFailed):
        outcome, status_code = UNAUTHENTICATED, 401
    elif isinstance(exc, Throttled) and not known:
        outcome, status_code = UNAUTHENTICATED, 429
    elif isinstance(exc, PermissionDenied):
        outcome, status_code = FORBIDDEN, 403
    else:
        return
    from aira_management.apps.api.access import source_of

    match = getattr(getattr(request, "_request", request), "resolver_match", None)
    say_refused(
        outcome=outcome,
        status=status_code,
        source_ip=source_of(getattr(request, "_request", request)),
        subject=str(user.get_username()) if known and user is not None else None,
        auth_method="oidc" if known else None,
        reason=_text(getattr(exc, "detail", "")) or None,
        route=getattr(match, "route", None) or None,
    )


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
    # Its type and where it was raised; the message, which may quote what the failing code had in
    # hand — `Key (username)=(…) already exists` — and the traceback only at `DEBUG` (`FRD-631`
    # FR-8).
    # The route, not the path: a path names whom it is about (`…/members/<username>/`).
    match = getattr(getattr(request, "_request", request), "resolver_match", None)
    _log.error(
        "unhandled_error",
        route=getattr(match, "route", None),
        method=getattr(request, "method", None),
        exc_info=exc,
        **trace_context_fields(),
    )
    return Response(envelope(_STATUS_CODES[500], INTERNAL_ERROR_MESSAGE), status=500)
