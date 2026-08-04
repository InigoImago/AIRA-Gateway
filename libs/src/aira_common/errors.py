"""Standard error model and exception type shared across AIRA services.

All API errors are serialized as an :class:`ErrorResponse` envelope so clients see a
consistent shape regardless of which component produced the error.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Machine- and human-readable description of a single error."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Top-level error envelope returned by AIRA APIs."""

    error: ErrorDetail


class AiraError(Exception):
    """Base class for expected, client-facing AIRA errors.

    Carries an HTTP status code and a stable machine-readable ``code`` so it can be
    turned into an :class:`ErrorResponse` by an API exception handler.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details

    def to_response(self) -> ErrorResponse:
        """Build the serializable error envelope for this exception."""
        return ErrorResponse(
            error=ErrorDetail(code=self.code, message=self.message, details=self.details)
        )


class NotFoundError(AiraError):
    """A requested resource does not exist."""

    status_code = 404
    code = "not_found"


class UnauthorizedError(AiraError):
    """Authentication is missing or invalid."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AiraError):
    """The caller is authenticated but not permitted to perform the action."""

    status_code = 403
    code = "forbidden"
