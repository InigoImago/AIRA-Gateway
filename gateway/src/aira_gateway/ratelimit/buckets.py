"""Token buckets for rate limiting (`FRD-405` §4.1).

A bucket holds ``capacity`` tokens and refills at ``refill_per_second``, computed from elapsed time
on each check — no timer, and an idle bucket costs nothing. A request takes ``cost`` tokens: one,
or **one per text of an embedding batch**, or a batch of 500 would turn 10 per minute into 5 000
texts (`FRD-113` §5.3). A bucket rather than fixed windows: a window lets twice the limit through
across a boundary and cannot tell a short burst from sustained flooding.

**A request is weighed against every bucket that applies to it, all or nothing**: taking from a
use-case bucket before finding the member bucket empty would charge the use case for a refused
request. That is a property of the interface, not a rule for callers to remember.

- :class:`RedisTokenBucket` is shared by every gateway instance: refill-test-take in one script.
- :class:`InMemoryTokenBucket` is per process — the fallback while Redis is unreachable, and what
  the hermetic tests use. On N instances it permits N × the limit: degraded, but bounded.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from aira_common.counters import CountersUnavailable, DegradationLog, ScriptRunner

#: The lowest rate a bucket is built with. Below it ``capacity / refill`` overflows the script's
#: expiry and the in-memory bucket answers ``inf`` seconds; callers already refuse a non-positive
#: rate, and the floor keeps the two implementations agreeing.
MINIMUM_RPM = 1

# Refill, test, take — over every bucket, in one indivisible step, on Redis' own clock (``TIME``):
# the instances sharing these buckets do not share a clock. Two passes on purpose: the first reads
# and decides, the second writes, so no bucket is debited before a later one could refuse.
_TAKE_TOKENS = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000.0

local cost = tonumber(ARGV[1])
local tokens = {}
local allowed = 1
local retry_ms = 0
local refused = 0

for i = 1, #KEYS do
  local capacity = tonumber(ARGV[(i - 1) * 2 + 2])
  local refill = tonumber(ARGV[(i - 1) * 2 + 3])

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

  if available < cost then
    local wait = math.ceil((cost - available) / refill * 1000)
    if allowed == 1 then refused = i end
    allowed = 0
    if wait > retry_ms then retry_ms = wait end
  end
end

-- The accrued refill is written back either way: time passed regardless of the decision. Only
-- the debit is conditional.
for i = 1, #KEYS do
  local capacity = tonumber(ARGV[(i - 1) * 2 + 2])
  local refill = tonumber(ARGV[(i - 1) * 2 + 3])
  local available = tokens[i]
  if allowed == 1 then available = available - cost end
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


def per_minute(
    key: str, limit_rpm: int, label: str = "", burst: int | None = None
) -> BucketRequest:
    """A ``limit_rpm`` requests-per-minute allowance, as the bucket that enforces it.

    The one constructor for a configured rate limit (`FRD-405`), the failed-authentication bound
    (`ADR-0015`) and a throttling suspension (`FRD-503`), so the three cannot drift. ``burst`` is
    the bucket's size; unset means the limit itself.
    """
    rate = max(MINIMUM_RPM, limit_rpm)
    return BucketRequest(
        key=key,
        capacity=burst if burst and burst > 0 else rate,
        refill_per_second=rate / 60,
        label=label,
    )


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
    async def take(self, requests: Sequence[BucketRequest], cost: int = 1) -> BucketDecision:
        """Take ``cost`` tokens from **every** bucket, or from none of them."""
        ...


class RedisTokenBucket:
    """Shared buckets. All gateway instances enforce one limit together."""

    def __init__(self, runner: ScriptRunner) -> None:
        self._runner = runner

    async def take(self, requests: Sequence[BucketRequest], cost: int = 1) -> BucketDecision:
        """Take ``cost`` tokens from each bucket, or report how long until they are available.

        Raises :class:`CountersUnavailable` when Redis cannot be reached; the caller falls back
        to the in-memory bucket rather than letting the request through (`FRD-405` §4.3).
        """
        if not requests:
            return ALLOWED
        args: list[str | int | float] = [cost]
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

    ``clock`` is injectable so refill over time can be tested without sleeping.
    """

    def __init__(self, clock: object = None) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._clock = clock or time.monotonic

    async def take(self, requests: Sequence[BucketRequest], cost: int = 1) -> BucketDecision:
        if not requests:
            return ALLOWED
        now = float(self._clock())  # type: ignore[operator]

        # The same two passes as the script: decide over all of them before debiting any.
        available: list[float] = []
        decision = ALLOWED
        for request in requests:
            tokens, ts = self._state.get(request.key, (float(request.capacity), now))
            tokens = min(
                float(request.capacity),
                tokens + max(0.0, now - ts) * request.refill_per_second,
            )
            available.append(tokens)
            if tokens < cost and decision.allowed:
                wait = (
                    (cost - tokens) / request.refill_per_second
                    if request.refill_per_second > 0
                    else float("inf")
                )
                decision = BucketDecision(allowed=False, retry_after_seconds=wait, refused=request)

        for request, tokens in zip(requests, available, strict=True):
            self._state[request.key] = (tokens - cost if decision.allowed else tokens, now)
        return decision


class FallbackTokenBucket:
    """The shared buckets, and the local ones whenever the shared store is unreachable.

    Not "allow everything": Redis being down coincides with infrastructure under strain, the worst
    moment to stop bounding a runaway caller who could exhaust the database pool for everyone.
    """

    FEATURE = "rate limiting"

    def __init__(
        self, shared: TokenBucket, local: TokenBucket, degradation: DegradationLog | None = None
    ) -> None:
        self._shared = shared
        self._local = local
        # `is not None`, not `or`: an empty log is falsy by design, and `or` would swap the
        # caller's log for a private one at construction, when nothing is degraded yet.
        self._degradation = degradation if degradation is not None else DegradationLog()

    @property
    def degraded(self) -> bool:
        return self.FEATURE in self._degradation.features

    async def take(self, requests: Sequence[BucketRequest], cost: int = 1) -> BucketDecision:
        try:
            decision = await self._shared.take(requests, cost)
        except CountersUnavailable:
            self._degradation.degraded(
                self.FEATURE, "per-instance buckets; N instances allow N x the limit"
            )
            return await self._local.take(requests, cost)
        self._degradation.working(self.FEATURE)
        return decision
