"""A bound on authentication *failures* from one source address.

Every `FRD-405` rate limit is keyed by a verified identity, so none of them bounds a caller who has
none; without this, credentials could be probed indefinitely at a database round trip each.

- **It counts refusals, not requests.** A working credential never touches this bucket, so the bound
  can be low without throttling any legitimate integration, however busy.
- **A shared source address shares a bucket.** Behind an untrusted proxy
  (`AIRA_TRUST_FORWARDED_FOR` off) every caller presents the proxy's address; the worst case is that
  somebody else's typo is answered 429 instead of 401 — working credentials are served throughout.
- **It degrades to per-instance, not to nothing** (`FRD-405`): without Redis the bound holds per
  process.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.persistence.recorder import client_ip
from aira_gateway.ratelimit.buckets import per_minute
from aira_gateway.ratelimit.service import RateLimitService
from aira_gateway.state import settings_of


async def record_failed_authentication(request: Request) -> None:
    """Take a token for this source address; raise 429 when it has none left.

    **Instead of** the 401: 429 with `Retry-After` is true — the credential was not judged — and it
    does not tell a prober whether the credential just tried was closer than the last.
    """
    settings = settings_of(request)
    limit = int(getattr(settings, "max_auth_failures_per_minute", 0) or 0)
    if limit <= 0:
        return
    # Annotated: a `None` here turns a security control off without a sound, so a wrong object must
    # be a build failure.
    service: RateLimitService | None = getattr(request.app.state, "rate_limits", None)
    bucket = getattr(service, "bucket", None)
    if bucket is None:
        return

    source = client_ip(request) or "unknown"
    decision = await bucket.take(
        [per_minute(f"authfail:{source}", limit, label="authentication failures")]
    )
    if decision.allowed:
        return
    raise GeminiHTTPError(
        429,
        "Too many failed authentication attempts from this address. Try again shortly.",
        "RESOURCE_EXHAUSTED",
        headers={"Retry-After": decision.retry_after_header},
    )
