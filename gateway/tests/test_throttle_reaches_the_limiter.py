"""A throttling suspension has to arrive at the thing that throttles (`FRD-503` FR-4).

`SuspensionService.check` produces a `Throttle`; `RateLimitService.check` consumes a
`BucketRequest`; `guard_before_work` passed the first straight into the second. They share no
field the limiter reads, so the *only* way a throttle could end was `AttributeError` — a **500**
for every request from the caller the decision was meant to slow down, while the console showed
the suspension as active and enforcing.

Nothing saw it. mypy could not: the two services meet through `app.state`, which is untyped, so
every call across that seam is unchecked. The tests could not either, and that is the instructive
half — `test_suspensions.py` asserts that `check` returns a throttle carrying `limit_rpm == 5`,
and stops exactly at the seam. Two correct halves and no wire, the fifth instance this project has
recorded after `record_to_outbox`, the missing Kafka topics, `payload_size` and the unannounced
catalog.

So the property under test here is deliberately not "a throttle object is built". It is **a
throttled caller is throttled**: the request goes through the gate a route uses, and the limit
that comes out of a suspension refuses the caller like any other.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_gateway.anomalies.suspensions import Throttle
from aira_gateway.api.serving import guard_before_work
from aira_gateway.ratelimit.buckets import InMemoryTokenBucket, per_minute
from aira_gateway.ratelimit.service import RateLimitService


class _StoppedCaller:
    """A suspension service that throttles everybody, at one request a minute."""

    def __init__(self, limit_rpm: int = 1) -> None:
        self._limit_rpm = limit_rpm

    async def check(self, use_case: Any, subject: Any, credential: Any) -> list[Throttle]:
        return [
            Throttle(label="suspension subject:ada", key="suspension:1", limit_rpm=self._limit_rpm)
        ]


class _NoBudgets:
    async def refuse_if_exhausted(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Sessions:
    """No use case is named, so no configured limit is ever loaded. The throttle is the only
    bucket in play — which is the case that was broken."""

    def __call__(self) -> Any:  # pragma: no cover - never reached
        raise AssertionError("a request naming no use case must not read the limit table")


class _Request:
    """The two attributes `guard_before_work` reads. A real `Request` would need a whole app."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.state = type("S", (), {})()


class _App:
    def __init__(self, state: Any) -> None:
        self.state = state


def _gate(limit_rpm: int = 1) -> _Request:
    bucket = InMemoryTokenBucket()
    state = type("State", (), {})()
    state.suspensions = _StoppedCaller(limit_rpm)
    state.rate_limits = RateLimitService(_Sessions(), bucket)
    state.budgets = _NoBudgets()
    return _Request(_App(state))


async def test_a_throttled_caller_is_refused_rather_than_crashing() -> None:
    """The whole defect in one case: before the fix this raised `AttributeError`, which the route
    turns into a 500 — a caller told the system is broken when it is working as configured."""
    request = _gate(limit_rpm=1)

    await guard_before_work(request)  # type: ignore[arg-type]

    from aira_gateway.ratelimit.errors import RateLimited

    with pytest.raises(RateLimited) as refusal:
        await guard_before_work(request)  # type: ignore[arg-type]
    # Named, so the caller learns which decision is holding them rather than only that something is.
    assert "suspension subject:ada" in str(refusal.value)


async def test_the_throttle_is_the_rate_it_says_it_is() -> None:
    """A throttle of 60/minute is a bucket of 60 refilling at one a second — the same reading
    `per_minute` gives a configured limit, because a rate written in one vocabulary and enforced
    in another is how the two come to disagree."""
    bucket = per_minute("suspension:1", 60, label="suspension")

    assert bucket.capacity == 60
    assert bucket.refill_per_second == pytest.approx(1.0)


async def test_a_throttle_never_divides_by_zero() -> None:
    """A rate of nothing per minute is not a bucket anybody can wait for: the in-memory
    implementation answers `inf` seconds and the Lua script's `EXPIRE` overflows. The floor is in
    `per_minute` so both implementations get it, rather than in the one that happened to guard."""
    bucket = per_minute("suspension:1", 0, label="suspension")

    assert bucket.refill_per_second > 0
    assert bucket.capacity >= 1
