"""Pipeline rejection — a request stopped before dispatch (FRD-300)."""

from __future__ import annotations


class PipelineRejected(Exception):
    """Raised when a pipeline step blocks a request. Carries a Gemini-shaped error mapping."""

    def __init__(self, message: str, *, code: int = 400, status: str = "INVALID_ARGUMENT") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
