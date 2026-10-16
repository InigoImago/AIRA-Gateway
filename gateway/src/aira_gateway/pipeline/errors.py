"""Pipeline rejection — a request stopped before dispatch (FRD-300)."""

from __future__ import annotations


class PipelineRejected(Exception):
    """Raised when a pipeline step blocks a request. Carries a Gemini-shaped error mapping."""

    def __init__(
        self,
        message: str,
        *,
        code: int = 400,
        status: str = "INVALID_ARGUMENT",
        step: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        #: Which step refused (`injection_filter`, `pii_filter`, …), so the refusal can say so in a
        #: form a program reads (`FRD-628` FR-3) without changing the sentence it already reads.
        self.step = step
