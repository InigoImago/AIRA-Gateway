"""Gemini-shaped error responses, shared by routes and the auth layer."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aira_gateway.api.gemini import schemas


def gemini_error_response(code: int, message: str, status: str) -> JSONResponse:
    """Build a Gemini error-envelope JSON response."""
    body = schemas.GeminiError(
        error=schemas.GeminiErrorDetail(code=code, message=message, status=status)
    )
    return JSONResponse(status_code=code, content=body.model_dump())


class GeminiHTTPError(Exception):
    """Raised to return a Gemini-shaped error (e.g. from an auth dependency)."""

    def __init__(self, code: int, message: str, status: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_response(self) -> JSONResponse:
        return gemini_error_response(self.code, self.message, self.status)
