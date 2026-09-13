"""A bound on failed authentications, keyed by source address (`ADR-0015`, this plane).

Limits keyed by use case or member need a verified identity, so they cannot bound somebody probing
credentials — and here each probe costs a JWKS verification.

**Not `AnonRateThrottle`**: DRF runs `check_permissions` before `check_throttles`, and every view
requires authentication, so an anonymous request is refused before any throttle runs. The bound
therefore lives in the authentication class, checked before the token is verified and recorded only
when it is rejected — **refusals only**, so a working credential never touches it.

Per process: Django's cache is `LocMemCache` unless a deployment configures one, so N workers admit
N × the rate. Bounded and imprecise beats unbounded.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

#: Cache key shape. Namespaced so it cannot collide with DRF's own scoped keys.
_KEY = "aira_auth_failures_%s"

#: Seconds per period name in DRF's `<n>/<period>` notation.
_PERIODS = {
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


class FailedAuthentications:
    """How many refusals one address may collect in a window, and whether it is over.

    Split into *check* and *record* rather than DRF's single `allow_request`, which counts every
    call — including the successful ones this must not count.
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
    """The source address as DRF resolves it (honouring `NUM_PROXIES`), so this bounds the same
    caller the rest of the stack sees."""
    return str(BaseThrottle().get_ident(request))


def _parse(rate: str) -> tuple[int, int]:
    """``"60/minute"`` → ``(60, 60)``. An unreadable rate switches the bound **off** rather than
    guessing: a bound nobody configured that starts refusing traffic is worse than none."""
    count, _, period = str(rate).partition("/")
    try:
        limit = int(count)
    except ValueError:
        return 0, 60
    return limit, _PERIODS.get(period.strip().lower(), 60)
