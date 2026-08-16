"""The atomic half of budget enforcement (FRD-405 §4.2).

``BudgetService.guard`` used to read the period's usage and decide; ``record`` booked the result
after dispatch. Everything in flight in between was invisible to every other request's guard, so
N concurrent requests all read the same stale figure and all passed. Since FRD-403 the limit is
a sum of money, which made that an accounting defect rather than a rounding one.

This module closes the window by making *check and reserve* one indivisible operation. A request
reserves what it expects to consume before it dispatches, so the next request's check already
accounts for it. When the response arrives the reservation is corrected to the real figure; when
the request fails it is released in full.

**Redis is a cache here, never the record.** The counter is seeded from ``budget_usage`` on a
miss, and Postgres continues to receive the authoritative figure after every request. Losing
Redis therefore costs the reservations currently in flight — not the period's accounting. The
counter also expires long before its budget period does, so any drift it picks up is corrected by
being rebuilt rather than carried to the end of the month.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.counters import ScriptRunner

# Both scripts operate on one hash per (scope, period): tokens / requests / cost.
#
# Note on magnitudes: the counters are stored and incremented as 64-bit integers by Redis
# itself, so the stored value is exact. Only the comparison below passes through Lua's double,
# which is exact to 2^53 — about nine million currency units in nano-units. Budgets of that size
# would lose sub-cent precision in the *comparison* and nowhere else.
_RESERVE = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then
  redis.call('HSET', key, 'tokens', ARGV[1], 'requests', ARGV[2], 'cost', ARGV[3])
end

-- **Before the limit checks, not after the debit.** The expiry used to be set on the last line,
-- which every *refused* reservation returns before reaching: a counter first created by a request
-- that immediately breached was left with no TTL at all, so it was never rebuilt from Postgres
-- and kept refusing on a figure the system of record did not share — for the rest of the period,
-- or until a request happened to pass. That is precisely the "permanent over-count nobody can
-- clear" `COUNTER_TTL_SECONDS` exists to make impossible, arriving through the one path that
-- skipped it. Refreshed on every touch, which is what a cache TTL means.
redis.call('EXPIRE', key, ARGV[10])

local current = redis.call('HMGET', key, 'tokens', 'requests', 'cost')
local tokens = tonumber(current[1]) or 0
local requests = tonumber(current[2]) or 0
local cost = tonumber(current[3]) or 0

local limit_tokens = tonumber(ARGV[7])
local limit_requests = tonumber(ARGV[8])
local limit_cost = tonumber(ARGV[9])

-- Checked before the reservation is added, which keeps the pre-existing meaning of a limit:
-- "already at it" is what refuses a request. The difference from before is that `current` now
-- includes what other in-flight requests have reserved.
if limit_cost >= 0 and cost >= limit_cost then return 'cost' end
if limit_requests >= 0 and requests >= limit_requests then return 'requests' end
if limit_tokens >= 0 and tokens >= limit_tokens then return 'tokens' end

redis.call('HINCRBY', key, 'tokens', ARGV[4])
redis.call('HINCRBY', key, 'requests', ARGV[5])
redis.call('HINCRBY', key, 'cost', ARGV[6])
return ''
"""

# Correcting or releasing a reservation. A key that no longer exists is left alone on purpose:
# the next reservation reseeds it from Postgres, which by then holds the settled figure.
_ADJUST = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then
  return 0
end
redis.call('HINCRBY', key, 'tokens', ARGV[1])
redis.call('HINCRBY', key, 'requests', ARGV[2])
redis.call('HINCRBY', key, 'cost', ARGV[3])

-- Clamp at zero. A counter cannot legitimately go negative, and one that did would silently
-- grant free headroom for the rest of the period instead of failing visibly.
local fields = {'tokens', 'requests', 'cost'}
for i = 1, #fields do
  if tonumber(redis.call('HGET', key, fields[i])) < 0 then
    redis.call('HSET', key, fields[i], 0)
  end
end
redis.call('EXPIRE', key, ARGV[4])
return 1
"""

# How long a counter is trusted before it is rebuilt from Postgres.
#
# Deliberately far shorter than the budget period. A counter can drift: if the correction after a
# request cannot reach Redis, the key keeps that request's (deliberately high) estimate, and a key
# that exists is never reseeded — so a long-lived counter would refuse traffic for the rest of the
# month against a figure Postgres does not share. Letting it expire makes the drift self-healing
# instead of permanent, and costs nothing: every reservation already reads the Postgres figure to
# seed with, so a rebuild is not an extra query.
#
# The trade is that reservations still in flight when a counter expires are forgotten, briefly
# reopening the race for whatever is in flight at that instant. Requests outlive this window only
# exceptionally, and a rare under-count of one estimate is a far better failure than a permanent
# over-count nobody can clear.
COUNTER_TTL_SECONDS = 300

NO_LIMIT = -1


@dataclass(frozen=True, slots=True)
class Amounts:
    """Tokens, request count and cost moved in one operation."""

    tokens: int = 0
    requests: int = 0
    cost_nanos: int = 0

    def negated(self) -> Amounts:
        return Amounts(-self.tokens, -self.requests, -self.cost_nanos)


@dataclass(frozen=True, slots=True)
class Limits:
    """The caps to test against; ``None`` means no cap on that dimension."""

    tokens: int | None = None
    requests: int | None = None
    cost_nanos: int | None = None


def _key(scope_key: str, period_key: str) -> str:
    return f"budget:{scope_key}:{period_key}"


class BudgetLedger:
    """Atomic reserve / settle / release over the shared counter store."""

    def __init__(self, runner: ScriptRunner) -> None:
        self._runner = runner

    async def reserve(
        self,
        scope_key: str,
        period_key: str,
        *,
        limits: Limits,
        amounts: Amounts,
        seed: Amounts,
    ) -> str | None:
        """Reserve ``amounts``, or return the name of the limit that refuses it.

        ``seed`` is the figure from Postgres, applied only if the counter does not exist yet.
        Raises :class:`~aira_common.counters.CountersUnavailable` if Redis cannot be reached; the
        caller then falls back to the Postgres path (FRD-405 §4.3).
        """
        breached = await self._runner.run(
            _RESERVE,
            [_key(scope_key, period_key)],
            [
                seed.tokens,
                seed.requests,
                seed.cost_nanos,
                amounts.tokens,
                amounts.requests,
                amounts.cost_nanos,
                NO_LIMIT if limits.tokens is None else limits.tokens,
                NO_LIMIT if limits.requests is None else limits.requests,
                NO_LIMIT if limits.cost_nanos is None else limits.cost_nanos,
                COUNTER_TTL_SECONDS,
            ],
        )
        return breached or None

    async def adjust(self, scope_key: str, period_key: str, *, amounts: Amounts) -> None:
        """Move a counter by ``amounts`` — a correction after settling, or a release."""
        await self._runner.run(
            _ADJUST,
            [_key(scope_key, period_key)],
            [amounts.tokens, amounts.requests, amounts.cost_nanos, COUNTER_TTL_SECONDS],
        )
