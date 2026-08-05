"""Token buckets for rate limiting (FRD-405 §4.1).

A bucket holds ``capacity`` tokens and refills at ``refill_per_second``. A request takes one
token; an empty bucket means the caller is over its limit. Refill is computed from elapsed time
on each check, so there is no timer and an idle bucket costs nothing.

Why a token bucket rather than a counter per fixed window: a fixed window lets twice the limit
through across a boundary (all of it in the last second of one minute, all of it again in the
first second of the next) and it cannot tell a short legitimate burst from sustained flooding.
A bucket expresses the actual intent — bursts are fine, a sustained flood is not.

**A request is weighed against every bucket that applies to it, all or nothing.** A caller may be
subject to a use-case bucket and a member bucket at once; taking a token from the first before
discovering the second is empty would charge the whole use case for a request that was refused,
so one throttled member could starve everybody else. Taking from all of them or from none is
therefore a property of the interface, not a rule callers are expected to remember.

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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from aira_common.counters import CountersUnavailable, DegradationLog, ScriptRunner

# Refill, test, take — over every bucket, in one indivisible step. Splitting this into a read and
# a write, or into one call per bucket, is precisely the race the shared bucket exists to remove.
#
# The clock is Redis' own (``TIME``), not the caller's: the instances sharing these buckets do not
# share a clock, and letting each one supply its own would make the refill rate depend on which
# instance happened to serve the request.
#
# Two passes on purpose. The first only *reads* and decides; the second writes. A single pass
# would have to debit each bucket before knowing whether a later one refuses, which is the defect
# this shape exists to make impossible.
_TAKE_TOKENS = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000.0

local tokens = {}
local allowed = 1
local retry_ms = 0
local refused = 0

for i = 1, #KEYS do
  local capacity = tonumber(ARGV[(i - 1) * 2 + 1])
  local refill = tonumber(ARGV[(i - 1) * 2 + 2])

  local state = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
  local available = tonumber(state[1])
  local ts = tonumber(state[2])
  if available == nil or ts == nil then
    available = capacity
    ts = now
  end

  local elapsed = now - ts
  if elapsed < 0 then elapsed = 0 end
  available = math.min(capacity, available + elapsed * refill)
  tokens[i] = available

  if available < 1 then
    local wait = math.ceil((1 - available) / refill * 1000)
    if allowed == 1 then refused = i end
    allowed = 0
    if wait > retry_ms then retry_ms = wait end
  end
end

-- The accrued refill is written back either way: time passed regardless of the decision. Only
-- the debit is conditional.
for i = 1, #KEYS do
  local capacity = tonumber(ARGV[(i - 1) * 2 + 1])
  local refill = tonumber(ARGV[(i - 1) * 2 + 2])
  local available = tokens[i]
  if allowed == 1 then available = available - 1 end
  redis.call('HSET', KEYS[i], 'tokens', tostring(available), 'ts', tostring(now))
  redis.call('EXPIRE', KEYS[i], math.max(60, math.ceil(capacity / refill) + 1))
end

return {allowed, retry_ms, refused}
"""


@dataclass(frozen=True, slots=True)
class BucketRequest:
    """One bucket a request must pass, and the terms it is judged on."""

    key: str
    capacity: int
    refill_per_second: float
    label: str = ""


@dataclass(frozen=True, slots=True)
class BucketDecision:
    """Whether the request may proceed, how long until it could, and which bucket refused."""

    allowed: bool
    retry_after_seconds: float = 0.0
    refused: BucketRequest | None = None

    @property
    def retry_after_header(self) -> str:
        """``Retry-After`` in whole seconds, never below 1 — a header of ``0`` invites the
        immediate retry the limit is trying to stop."""
        return str(max(1, math.ceil(self.retry_after_seconds)))


ALLOWED = BucketDecision(allowed=True)


class TokenBucket(Protocol):
    async def take(self, requests: Sequence[BucketRequest]) -> BucketDecision:
        """Take one token from **every** bucket, or from none of them."""
        ...


class RedisTokenBucket:
    """Shared buckets. All gateway instances enforce one limit together."""

    def __init__(self, runner: ScriptRunner) -> None:
        self._runner = runner

    async def take(self, requests: Sequence[BucketRequest]) -> BucketDecision:
        """Take a token from each bucket, or report how long until one is available.

        Raises :class:`CountersUnavailable` when Redis cannot be reached — the caller falls back
        to the in-memory bucket rather than letting the request through (FRD-405 §4.3).
        """
        if not requests:
            return ALLOWED
        args: list[str | int | float] = []
        for request in requests:
            args.extend((request.capacity, request.refill_per_second))
        allowed, retry_ms, refused = await self._runner.run(
            _TAKE_TOKENS, [request.key for request in requests], args
        )
        index = int(refused)
        return BucketDecision(
            allowed=bool(int(allowed)),
            retry_after_seconds=int(retry_ms) / 1000,
            refused=requests[index - 1] if index else None,
        )


class InMemoryTokenBucket:
    """Per-process buckets: the fallback while Redis is unreachable, and what unit tests use.

    ``clock`` is injectable so refill over time can be tested without sleeping — a limiter whose
    time-dependent behaviour is only ever exercised at one instant is barely tested at all.
    """

    def __init__(self, clock: object = None) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._clock = clock or time.monotonic

    async def take(self, requests: Sequence[BucketRequest]) -> BucketDecision:
        if not requests:
            return ALLOWED
        now = float(self._clock())  # type: ignore[operator]

        # Same two passes as the Lua, for the same reason: decide over all of them before
        # debiting any of them.
        available: list[float] = []
        decision = ALLOWED
        for request in requests:
            tokens, ts = self._state.get(request.key, (float(request.capacity), now))
            tokens = min(
                float(request.capacity),
                tokens + max(0.0, now - ts) * request.refill_per_second,
            )
            available.append(tokens)
            if tokens < 1 and decision.allowed:
                wait = (
                    (1 - tokens) / request.refill_per_second
                    if request.refill_per_second > 0
                    else float("inf")
                )
                decision = BucketDecision(allowed=False, retry_after_seconds=wait, refused=request)

        for request, tokens in zip(requests, available, strict=True):
            self._state[request.key] = (tokens - 1 if decision.allowed else tokens, now)
        return decision


class FallbackTokenBucket:
    """Uses the shared buckets, and the local ones whenever the shared store is unreachable.

    The fallback is not "allow everything". Redis being down coincides with infrastructure
    already under strain, which is the worst moment to stop bounding a runaway caller: one
    client can exhaust the database connection pool and take every other use case down with it.
    Per-process buckets are imprecise across instances and still prevent that.
    """

    FEATURE = "rate limiting"

    def __init__(
        self, shared: TokenBucket, local: TokenBucket, degradation: DegradationLog | None = None
    ) -> None:
        self._shared = shared
        self._local = local
        # `is not None`, not `or`: an empty log is falsy by design (so `if degradation:` reads
        # as "is anything degraded"), and `or` would quietly swap a caller's log for a private
        # one at exactly the moment nothing was wrong yet — which is always, at construction.
        self._degradation = degradation if degradation is not None else DegradationLog()

    @property
    def degraded(self) -> bool:
        return self.FEATURE in self._degradation.features

    async def take(self, requests: Sequence[BucketRequest]) -> BucketDecision:
        try:
            decision = await self._shared.take(requests)
        except CountersUnavailable:
            self._degradation.degraded(
                self.FEATURE, "per-instance buckets; N instances allow N x the limit"
            )
            return await self._local.take(requests)
        self._degradation.working(self.FEATURE)
        return decision
