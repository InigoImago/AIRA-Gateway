"""A bound on failed authentications, keyed by source address (`ADR-0015`, this plane).

The gateway has had one since 2026-08-08 and states the argument there:

> Every limit `FRD-405` built is keyed by use case or member, so it needs a **verified** identity
> and cannot bound the traffic of somebody who has none — an unauthenticated caller could probe
> credentials, and each attempt a database round trip, without ever meeting a bound.

Management had none, and a request here is more expensive than one there: a presented token is
verified against the issuer's JWKS before anything decides it is invalid.

**Not `AnonRateThrottle`.** That was the obvious answer and it cannot work: DRF runs
`check_permissions` before `check_throttles`, and every view here requires authentication — so an
unauthenticated request is refused at the permission check and the throttle never runs. Measured
before this module existed: two anonymous requests against a rate of one per minute, both `401`,
the second never counted. A throttle that cannot fire is the badge-wearing absent control this
project keeps naming, and shipping one would have been worse than shipping nothing.

So the bound lives where the failure does — in the authentication class, checked before the token
is verified and recorded only when it is rejected. **Refusals only**, exactly as the gateway counts
them: a working credential never touches this bucket however busy its holder is.

Per process, like every other DRF throttle here: Django's cache is `LocMemCache` unless a
deployment configures one, so N workers admit N × the rate. Bounded and imprecise beats unbounded
(`FallbackTokenBucket` makes the same trade on the other plane).
"""

from __future__ import annotations

import time
from typing import Any

from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

#: Cache key shape. Namespaced so it cannot collide with DRF's own scoped keys.
_KEY = "aira_auth_failures_%s"


class FailedAuthentications:
    """How many refusals one address may collect in a window, and whether it is over.

    Split into *check* and *record* rather than DRF's single `allow_request`, because that one
    counts every call — including the successful ones this must not count.
    """

    def __init__(self, rate: str) -> None:
        self._limit, self._window = _parse(rate)

    @property
    def enabled(self) -> bool:
        """A rate of zero switches it off — for an installation whose WAF already does this."""
        return self._limit > 0

    def over_the_bound(self, request: Any, *, now: float | None = None) -> bool:
        if not self.enabled:
            return False
        return len(self._recent(request, now or time.time())) >= self._limit

    def record_failure(self, request: Any, *, now: float | None = None) -> None:
        if not self.enabled:
            return
        moment = now or time.time()
        history = self._recent(request, moment)
        history.insert(0, moment)
        cache.set(_KEY % _ident(request), history, self._window)

    def retry_after(self, request: Any, *, now: float | None = None) -> int:
        """Whole seconds until the oldest attempt in the window ages out. Never below one — a
        `Retry-After: 0` invites the immediate retry the bound exists to stop."""
        moment = now or time.time()
        history = self._recent(request, moment)
        if not history:
            return 1
        return max(1, int(self._window - (moment - history[-1])) + 1)

    def _recent(self, request: Any, now: float) -> list[float]:
        history: list[float] = list(cache.get(_KEY % _ident(request), []))
        cutoff = now - self._window
        while history and history[-1] <= cutoff:
            history.pop()
        return history


def _ident(request: Any) -> str:
    """The source address, as DRF resolves it.

    Borrowed from `BaseThrottle.get_ident` rather than reimplemented: it already honours
    `NUM_PROXIES`, and an address this reads differently from the rest of the stack would bound a
    different caller than the one being refused.
    """
    return str(BaseThrottle().get_ident(request))


def _parse(rate: str) -> tuple[int, int]:
    """``"60/minute"`` → ``(60, 60)``. An unreadable rate switches the bound **off** rather than
    guessing: a bound nobody configured that starts refusing traffic is worse than none."""
    periods = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "h": 3600,
        "hour": 3600,
        "d": 86400,
        "day": 86400,
    }
    count, _, period = str(rate).partition("/")
    try:
        limit = int(count)
    except ValueError:
        return 0, 60
    return limit, periods.get(period.strip().lower(), 60)
