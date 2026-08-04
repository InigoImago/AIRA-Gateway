"""Budget enforcement error (FRD-401)."""

from __future__ import annotations


class BudgetExceeded(Exception):
    """Raised when a request would exceed a configured budget (maps to 429)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
