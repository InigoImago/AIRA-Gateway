"""Budget enforcement error (FRD-401)."""

from __future__ import annotations


class BudgetExceeded(Exception):
    """Raised when a request would exceed a configured budget (maps to 429)."""

    def __init__(self, message: str, resets_at: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        #: When the budget's period ends and it takes requests again, ISO 8601 with the offset of
        #: the installation's clock (`FRD-628` FR-6, `FRD-629`). In the message and in Google's
        #: `ErrorInfo`, never as `Retry-After`: a client that honours that header would sleep until
        #: the month is over.
        self.resets_at = resets_at
