"""Rate-limit rejection (FRD-405)."""

from __future__ import annotations


class RateLimited(Exception):
    """Raised pre-dispatch when a caller is over its configured request rate.

    Carries ``retry_after`` so the response can tell the client when to come back. Without it a
    well-behaved client has no choice but to retry immediately, which turns a limit into a busy
    loop against the thing it is meant to protect.
    """

    def __init__(self, message: str, retry_after: str) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
