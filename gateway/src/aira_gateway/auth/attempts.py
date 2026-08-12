"""A bound on authentication *failures* from one source address (2026-08-08).

`FRD-405` gave the gateway rate limits, and every one of them is keyed by use case or by member —
which means they need a verified identity and therefore cannot bound the traffic of somebody who
has none. An unauthenticated caller could probe credentials indefinitely, each attempt costing a
database round trip, and never meet a limit. The body ceiling bounds one request's *size*; nothing
bounded their *number*.

Two decisions worth keeping:

**It counts refusals, not requests.** A caller presenting a working credential never touches this
bucket, so no legitimate integration can be throttled by it however busy it is — and the bound can
therefore be low enough to be worth having. A per-address limit on *all* traffic would have to be
set high enough for the busiest legitimate client behind a shared NAT, which is to say high enough
to be useless.

**A shared source address shares a bucket**, and that is tolerable only because of the first
decision. Behind a proxy that this deployment does not trust (`AIRA_TRUST_FORWARDED_FOR` off, the
safe default for the audit trail), every caller presents the proxy's address. One prober can then
exhaust the bucket for everybody — but "everybody" here means everybody whose credential *also*
fails: a working credential is served throughout. The blast radius of the worst case is that
somebody else's typo is answered 429 instead of 401.

**It degrades to per-instance, not to nothing.** The same decision `FRD-405` made: the moment a
control stops working is the worst moment to stop applying it. Without Redis the bound holds per
process, which is weaker and is not none.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.persistence.recorder import client_ip
from aira_gateway.ratelimit.buckets import per_minute
from aira_gateway.ratelimit.service import RateLimitService
from aira_gateway.state import settings_of

#: The bound is expressed per minute, which is what :func:`per_minute` builds. Kept as a name
#: because the docstring above and the setting's own comment both say "per minute".
WINDOW_SECONDS = 60.0


async def record_failed_authentication(request: Request) -> None:
    """Take a token for this source address; raise 429 when it has none left.

    Raising **instead of** the 401 is deliberate. 429 with `Retry-After` is a true statement — the
    credential was not judged, the caller is being asked to slow down — and it does not tell a
    prober whether the credential they just tried was closer than the last one.
    """
    settings = settings_of(request)
    limit = int(getattr(settings, "max_auth_failures_per_minute", 0) or 0)
    if limit <= 0:
        return
    # Annotated: this is the one read whose `None` branch turns a **security** control off
    # without a sound, so the declared type is what makes a rename or a wrong object a build
    # failure rather than a bound that quietly stops counting.
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
