"""DRF exception handler producing a consistent error envelope (FRD-200).

Shape mirrors the gateway: ``{"error": {"code", "message", "details"}}``.
"""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

_STATUS_CODES = {
    400: "invalid_argument",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    if isinstance(data, dict) and "detail" in data:
        message = str(data["detail"])
        details = None
    else:
        message = "Request failed."
        details = data

    response.data = {
        "error": {
            "code": _STATUS_CODES.get(response.status_code, "error"),
            "message": message,
            "details": details,
        }
    }
    return response
