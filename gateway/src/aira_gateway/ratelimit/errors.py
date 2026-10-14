"""Rate-limit rejection (FRD-405)."""

from __future__ import annotations

from aira_gateway.api.gemini.errors import GeminiHTTPError


class RateLimited(Exception):
    """Raised pre-dispatch when a caller is over its configured request rate.

    Carries ``retry_after`` so the response can tell the client when to come back. Without it a
    well-behaved client has no choice but to retry immediately, which turns a limit into a busy
    loop against the thing it is meant to protect.
    """

    def __init__(self, message: str, retry_after: str, bucket: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        #: The key of the bucket that refused, so a caller that added one of its own — a
        #: suspension's throttle (`FRD-503`) — can tell its refusal from a configured limit's.
        self.bucket = bucket


class TooHeavyForTheLimit(GeminiHTTPError):
    """A request weighing more than a bucket can ever hold — a batch larger than the allowance.

    **Not a `RateLimited`.** No wait admits it, so a `429` with a `Retry-After` sends a client that
    honours both into a retry loop that never ends. A `400 FAILED_PRECONDITION`, without one: the
    request has to change, not the moment it is sent (`FRD-113` §11). Every surface renders the
    type it extends, and the audit row reads it as an invalid request.
    """

    def __init__(self, message: str) -> None:
        super().__init__(400, message, "FAILED_PRECONDITION")
