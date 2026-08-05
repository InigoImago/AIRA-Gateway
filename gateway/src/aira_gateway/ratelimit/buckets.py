"""Token buckets for rate limiting (FRD-405 §4.1).

A bucket holds ``capacity`` tokens and refills at ``refill_per_second``. A request takes one
token; an empty bucket means the caller is over its limit. Refill is computed from elapsed time
on each check, so there is no timer and an idle bucket costs nothing.

Why a token bucket rather than a counter per fixed window: a fixed window lets twice the limit
through across a boundary (all of it in the last second of one minute, all of it again in the
first second of the next) and it cannot tell a short legitimate burst from sustained flooding.
A bucket expresses the actual intent — bursts are fine, a sustained flood is not.

Two implementations, and the difference matters:

- :class:`RedisTokenBucket` is shared by every gateway instance and does refill-test-take in one
  Lua script, so two instances behind a load balancer enforce one limit rather than one each.
- :class:`InMemoryTokenBucket` is per process. It is the fallback when Redis is unreachable, and
  what the hermetic tests use. On N instances it permits N × the limit — degraded, but bounded,
  which at the moment Redis is down is worth considerably more than letting everything through.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Protocol

from aira_common.counters import CountersUnavailable, ScriptRunner

# Refill, test, take — in one indivisible step. Splitting this into a read and a write is
# precisely the race the shared bucket exists to remove.
#
# The clock is Redis' own (``TIME``), not the caller's: the instances sharing this bucket do not
# share a clock, and letting each one supply its own would make the refill rate depend on which
# instance happened to serve the request.
_TAKE_TOKEN = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000.0

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) / refill * 1000)
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, retry_ms}
"""


@dataclass(frozen=True, slots=True)
class BucketDecision:
    """Whether the request may proceed, and how long until a token is available if not."""

    allowed: bool
    retry_after_seconds: float

    @property
    def retry_after_header(self) -> str:
        """``Retry-After`` in whole seconds, never below 1 — a header of ``0`` invites the
        immediate retry the limit is trying to stop."""
        return str(max(1, math.ceil(self.retry_after_seconds)))


class TokenBucket(Protocol):
    async def take(self, key: str, capacity: int, refill_per_second: float) -> BucketDecision: ...


def _ttl_seconds(capacity: int, refill_per_second: float) -> int:
    """How long an untouched bucket needs to refill completely.

    Expiring earlier would hand a caller a full bucket sooner than it earned one; expiring later
    just keeps a key around that is indistinguishable from a fresh one.
    """
    if refill_per_second <= 0:
        return 60
    return max(60, math.ceil(capacity / refill_per_second) + 1)


class RedisTokenBucket:
    """Shared bucket. All gateway instances enforce one limit together."""

    def __init__(self, runner: ScriptRunner) -> None:
        self._runner = runner

    async def take(self, key: str, capacity: int, refill_per_second: float) -> BucketDecision:
        """Take a token, or report how long until one is available.

        Raises :class:`CountersUnavailable` when Redis cannot be reached — the caller falls back
        to the in-memory bucket rather than letting the request through (FRD-405 §4.3).
        """
        allowed, retry_ms = await self._runner.run(
            _TAKE_TOKEN,
            [key],
            [capacity, refill_per_second, _ttl_seconds(capacity, refill_per_second)],
        )
        return BucketDecision(allowed=bool(int(allowed)), retry_after_seconds=int(retry_ms) / 1000)


class InMemoryTokenBucket:
    """Per-process bucket: the fallback while Redis is unreachable, and what unit tests use.

    ``clock`` is injectable so refill over time can be tested without sleeping — a limiter whose
    time-dependent behaviour is only ever exercised at one instant is barely tested at all.
    """

    def __init__(self, clock: object = None) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._clock = clock or time.monotonic

    async def take(self, key: str, capacity: int, refill_per_second: float) -> BucketDecision:
        now = float(self._clock())  # type: ignore[operator]
        tokens, ts = self._state.get(key, (float(capacity), now))
        tokens = min(float(capacity), tokens + max(0.0, now - ts) * refill_per_second)

        if tokens >= 1:
            self._state[key] = (tokens - 1, now)
            return BucketDecision(allowed=True, retry_after_seconds=0.0)

        self._state[key] = (tokens, now)
        wait = (1 - tokens) / refill_per_second if refill_per_second > 0 else float("inf")
        return BucketDecision(allowed=False, retry_after_seconds=wait)


class FallbackTokenBucket:
    """Uses the shared bucket, and the local one whenever the shared one is unreachable.

    The fallback is not "allow everything". Redis being down coincides with infrastructure
    already under strain, which is the worst moment to stop bounding a runaway caller: one
    client can exhaust the database connection pool and take every other use case down with it.
    A per-process bucket is imprecise across instances and still prevents that.
    """

    def __init__(self, shared: TokenBucket, local: TokenBucket) -> None:
        self._shared = shared
        self._local = local
        self.degraded = False

    async def take(self, key: str, capacity: int, refill_per_second: float) -> BucketDecision:
        try:
            decision = await self._shared.take(key, capacity, refill_per_second)
        except CountersUnavailable:
            self.degraded = True
            return await self._local.take(key, capacity, refill_per_second)
        self.degraded = False
        return decision
