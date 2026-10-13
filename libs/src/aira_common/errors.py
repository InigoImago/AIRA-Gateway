"""The AIRA error envelope: ``{"error": {"code", "message", "details"}}``.

The gateway's handlers answer every AIRA-path failure in it, and Management's DRF handler builds the
same shape, so a client reading both planes reads one envelope.
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
