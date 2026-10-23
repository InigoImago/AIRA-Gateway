"""The atomic half of budget enforcement: reservations in Redis (`FRD-405` §4.2).

*Check and reserve* are one indivisible operation, so a request's reservation is visible to the
next request's check. The reservation is corrected to the real figure when the response arrives,
or released in full when the request fails.

**Redis is a cache here, never the record.** A counter is seeded from ``budget_usage`` on a miss and
Postgres receives the authoritative figure after every request, so losing Redis costs the
reservations in flight, not the period's accounting.

**A rebuilt counter keeps the reservations in flight.** Postgres holds what finished requests
spent and nothing of what running ones set aside, so a counter rebuilt from it alone forgets every
reservation still open — and each of their settlements then subtracts a reservation the counter no
longer holds. Each reservation is therefore also written, under its own id and with a deadline,
beside the counter; a rebuild adds those still open, and settling or releasing one removes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.counters import ScriptRunner

#: How long a counter is trusted before it is rebuilt from Postgres — far shorter than any budget
#: period. A counter can drift (a correction that cannot reach Redis leaves the request's high
#: estimate behind, and an existing key is never reseeded); expiry makes that drift heal itself
#: instead of refusing traffic for the rest of the month. The trade: reservations in flight when a
#: counter expires are forgotten — a rare under-count, over a permanent over-count.
#:
#: **Set once, when the counter is built, and never extended.** Refreshed on every reservation, the
#: expiry would be an idle timeout: a counter seeing a request every few minutes would never be
#: rebuilt, and its drift would last the period on exactly the busy counters where it matters.
COUNTER_TTL_SECONDS = 300

#: How long a reservation in flight is carried into a rebuilt counter. Longer than any request
#: holds one — a stream drained after its caller left included — so a rebuild forgets only a
#: reservation whose request is gone; one that could never be removed (Redis away when it settled)
#: over-counts for at most this long, in the direction that refuses rather than spends.
RESERVATION_LIFETIME_SECONDS = 3600

#: The limit argument that means "no cap on this dimension".
NO_LIMIT = -1

# Both scripts work on one hash per (scope, period): tokens / requests / cost. Redis stores and
# increments 64-bit integers exactly; only the comparison passes through Lua's double, which is
# exact to 2^53 nano-units — about nine million currency units. Above that it is off by at most the
# double's step there: 16 nano-units at Management's largest cost limit (99 999 999.999999).
_RESERVE = """
local key = KEYS[1]
local open = KEYS[2]
if redis.call('EXISTS', key) == 0 then
  redis.call('HSET', key, 'tokens', ARGV[1], 'requests', ARGV[2], 'cost', ARGV[3])
  -- Postgres knows what finished requests spent; the reservations still open are added back, or
  -- their settlements subtract from the rebuilt counter what it never held. Each entry is
  -- `deadline:tokens:requests:cost`; one past its deadline belongs to no running request.
  local now = tonumber(ARGV[12])
  local entries = redis.call('HGETALL', open)
  for i = 1, #entries, 2 do
    local deadline, t, r, c = string.match(entries[i + 1], '^(%d+):(%-?%d+):(%-?%d+):(%-?%d+)$')
    if deadline and tonumber(deadline) > now then
      redis.call('HINCRBY', key, 'tokens', t)
      redis.call('HINCRBY', key, 'requests', r)
      redis.call('HINCRBY', key, 'cost', c)
    else
      redis.call('HDEL', open, entries[i])
    end
  end
end

-- The expiry is set where the counter has none — when it is built — and never extended, so the
-- counter is rebuilt from Postgres on a fixed clock however busy it is. Before the limit checks: a
-- refused reservation returns early, and a counter it created must still expire.
if redis.call('TTL', key) == -1 then
  redis.call('EXPIRE', key, ARGV[10])
end

local current = redis.call('HMGET', key, 'tokens', 'requests', 'cost')
local tokens = tonumber(current[1]) or 0
local requests = tonumber(current[2]) or 0
local cost = tonumber(current[3]) or 0

local limit_tokens = tonumber(ARGV[7])
local limit_requests = tonumber(ARGV[8])
local limit_cost = tonumber(ARGV[9])

-- Checked before the reservation is added: "already at the limit" is what refuses, and `current`
-- includes what other in-flight requests have reserved.
if limit_cost >= 0 and cost >= limit_cost then return 'cost' end
if limit_requests >= 0 and requests >= limit_requests then return 'requests' end
if limit_tokens >= 0 and tokens >= limit_tokens then return 'tokens' end

redis.call('HINCRBY', key, 'tokens', ARGV[4])
redis.call('HINCRBY', key, 'requests', ARGV[5])
redis.call('HINCRBY', key, 'cost', ARGV[6])
if ARGV[11] ~= '' then
  redis.call('HSET', open, ARGV[11], ARGV[13])
  redis.call('EXPIRE', open, ARGV[14])
end
return ''
"""

# Correcting or releasing a reservation. A missing key is left alone: the next reservation reseeds
# it from Postgres, which by then holds the settled figure. The reservation leaves the open set
# first, whether or not the counter is there: resolved, it is in Postgres or nowhere, and a rebuild
# that added it back would count it twice.
_ADJUST = """
local key = KEYS[1]
if ARGV[5] ~= '' then
  redis.call('HDEL', KEYS[2], ARGV[5])
end
if redis.call('EXISTS', key) == 0 then
  return 0
end
redis.call('HINCRBY', key, 'tokens', ARGV[1])
redis.call('HINCRBY', key, 'requests', ARGV[2])
redis.call('HINCRBY', key, 'cost', ARGV[3])

-- Clamp at zero: a negative counter would grant free headroom for the rest of the period.
local fields = {'tokens', 'requests', 'cost'}
for i = 1, #fields do
  if tonumber(redis.call('HGET', key, fields[i])) < 0 then
    redis.call('HSET', key, fields[i], 0)
  end
end
-- A correction does not extend the counter's life (see `_RESERVE`); it only gives one to a counter
-- that has none.
if redis.call('TTL', key) == -1 then
  redis.call('EXPIRE', key, ARGV[4])
end
return 1
"""


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
    """The caps to test against; ``None`` means no cap on that dimension.

    A **negative** cap allows nothing, as the Postgres path reads it (`usage >= limit`): the two
    enforcement paths must not answer one stored figure in opposite ways, and a figure no author
    could have meant is read as the one that refuses.
    """

    tokens: int | None = None
    requests: int | None = None
    cost_nanos: int | None = None


def _cap(limit: int | None) -> int:
    """A limit as the script's argument: `NO_LIMIT` for none, never below zero for one."""
    return NO_LIMIT if limit is None else max(0, limit)


def _key(scope_key: str, period_key: str) -> str:
    return f"budget:{scope_key}:{period_key}"


def _open_key(scope_key: str, period_key: str) -> str:
    """Where the counter's reservations in flight are kept, beside it (module docstring)."""
    return f"budget:{scope_key}:{period_key}:open"


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
        reservation: str = "",
        at: float = 0.0,
    ) -> str | None:
        """Reserve ``amounts``, or return the name of the limit that refuses it.

        ``seed`` is the Postgres figure, applied only if the counter does not exist yet — with the
        reservations still open added to it. ``reservation`` names this one among them, so a later
        rebuild carries it until it is settled, released, or ``at`` (the reserving moment, in
        seconds since 1970) is `RESERVATION_LIFETIME_SECONDS` past; without a name it is reserved
        and carried by nothing. Raises :class:`~aira_common.counters.CountersUnavailable` if Redis
        cannot be reached; the caller then falls back to the Postgres path (`FRD-405` §4.3).
        """
        now = int(at)
        deadline = now + RESERVATION_LIFETIME_SECONDS
        entry = f"{deadline}:{amounts.tokens}:{amounts.requests}:{amounts.cost_nanos}"
        breached = await self._runner.run(
            _RESERVE,
            [_key(scope_key, period_key), _open_key(scope_key, period_key)],
            [
                seed.tokens,
                seed.requests,
                seed.cost_nanos,
                amounts.tokens,
                amounts.requests,
                amounts.cost_nanos,
                _cap(limits.tokens),
                _cap(limits.requests),
                _cap(limits.cost_nanos),
                COUNTER_TTL_SECONDS,
                reservation,
                now,
                entry,
                RESERVATION_LIFETIME_SECONDS,
            ],
        )
        return breached or None

    async def adjust(
        self, scope_key: str, period_key: str, *, amounts: Amounts, reservation: str = ""
    ) -> None:
        """Move a counter by ``amounts`` — a correction after settling, or a release. Settling or
        releasing ``reservation`` also closes it, so no rebuild adds it back."""
        await self._runner.run(
            _ADJUST,
            [_key(scope_key, period_key), _open_key(scope_key, period_key)],
            [
                amounts.tokens,
                amounts.requests,
                amounts.cost_nanos,
                COUNTER_TTL_SECONDS,
                reservation,
            ],
        )
