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

**A correction is never lost to an unreachable store.** One the store cannot take is held by this
ledger and sent before anything else is asked of the store. Dropped, it would leave its reservation
open beside the counter, and every rebuild would add the request's estimate back on top of the
figure Postgres already holds, until the reservation's deadline. A correction moves the counter only
while its reservation is open, so one sent twice moves it once.

**A rebuild and a booking are told apart by Postgres' own request count.** A booking commits to
Postgres and then corrects the counter, and a rebuild reads Postgres and then builds — so a rebuild
can fall between a booking's two halves and count it twice, or read before the commit and build
after the correction and count it not at all. Every booking counts a request, so a row's request
count orders its readings: the counter keeps the one it was built on (its basis), and a correction
brings the row as its booking left it. A correction for a booking the basis already holds takes back
only what the reservation set aside; the row it brings is kept beside the counter, and a rebuild
builds on whichever of it and its own seed is later.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from aira_common.counters import CountersNotAsked, CountersUnavailable, ScriptArg, ScriptRunner
from aira_common.logging import get_logger

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
#: reservation whose request is gone. One whose correction never reached the store — held past this
#: long, or by a process that ended — over-counts for at most this long, in the direction that
#: refuses rather than spends.
RESERVATION_LIFETIME_SECONDS = 3600

#: The most corrections one process holds for an unreachable store; past it the oldest gives way.
#: Only a reservation made before the store went away has a correction to hold — a request admitted
#: while it is away reserves nothing there — so this is far more than one instance has in flight.
HELD_CORRECTIONS_LIMIT = 10_000

#: How many held corrections one call sends before its own work, so the first request after an
#: outage is not the one that pays for all of it; the next calls send the rest.
RESEND_PER_CALL = 256

#: The limit argument that means "no cap on this dimension".
NO_LIMIT = -1

_log = get_logger("aira_gateway.budgets")

# Both scripts work on one hash per (scope, period): tokens / requests / cost. Redis stores and
# increments 64-bit integers exactly; only the comparison passes through Lua's double, which is
# exact to 2^53 nano-units — about nine million currency units. Above that it is off by at most the
# double's step there: 16 nano-units at Management's largest cost limit (99 999 999.999999).
_RESERVE = """
local key = KEYS[1]
local open = KEYS[2]
if redis.call('EXISTS', key) == 0 then
  -- Built on the later of two readings of Postgres: the seed, read before this script ran, and the
  -- row as the last booking left it, kept beside the counter by that booking's correction. A seed
  -- read just before a booking would miss it otherwise, the correction having found no counter.
  -- The request count says which reading is later; the counter keeps it as its basis.
  local base = {ARGV[1], ARGV[2], ARGV[3]}
  local booked = redis.call('HMGET', KEYS[3], 'tokens', 'requests', 'cost')
  if booked[2] and tonumber(booked[2]) > tonumber(base[2]) then
    base = booked
  end
  redis.call('HSET', key, 'tokens', base[1], 'requests', base[2], 'cost', base[3], 'basis', base[2])
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

# Correcting or releasing a reservation, or adding a figure no reservation held. A missing key is
# left alone: the next reservation reseeds it from Postgres, which by then holds the settled figure.
# The reservation leaves the open set first, whether or not the counter is there: resolved, it is in
# Postgres or nowhere, and a rebuild that added it back would count it twice. Where Postgres booked
# something (ARGV[10] set), the row as the booking left it (ARGV[9..11]) is kept beside the counter
# while it is the latest known, for a rebuild whose seed was read before it.
_ADJUST = """
local key = KEYS[1]
local booked = ARGV[10] ~= ''
if booked then
  local known = redis.call('HGET', KEYS[3], 'requests')
  if not known or tonumber(ARGV[10]) > tonumber(known) then
    redis.call('HSET', KEYS[3], 'tokens', ARGV[9], 'requests', ARGV[10], 'cost', ARGV[11])
    redis.call('EXPIRE', KEYS[3], ARGV[4])
  end
end
-- Only an open reservation moves the counter: settled or released once, it is closed, and a second
-- resolution — or the same correction sent again after its reply was lost — moves nothing.
if ARGV[5] ~= '' and redis.call('HDEL', KEYS[2], ARGV[5]) == 0 then
  return 0
end
if redis.call('EXISTS', key) == 0 then
  return 0
end
-- A counter whose basis already holds this booking has it once: only what the reservation set
-- aside comes back out (ARGV[6..8]).
local tokens, requests, cost = ARGV[1], ARGV[2], ARGV[3]
if booked and tonumber(redis.call('HGET', key, 'basis') or -1) >= tonumber(ARGV[10]) then
  tokens, requests, cost = ARGV[6], ARGV[7], ARGV[8]
end
redis.call('HINCRBY', key, 'tokens', tokens)
redis.call('HINCRBY', key, 'requests', requests)
redis.call('HINCRBY', key, 'cost', cost)

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


#: Nothing moved: what a figure no reservation held has set aside.
_NOTHING = Amounts()


def _cap(limit: int | None) -> int:
    """A limit as the script's argument: `NO_LIMIT` for none, never below zero for one."""
    return NO_LIMIT if limit is None else max(0, limit)


def _key(scope_key: str, period_key: str) -> str:
    return f"budget:{scope_key}:{period_key}"


def _open_key(scope_key: str, period_key: str) -> str:
    """Where the counter's reservations in flight are kept, beside it (module docstring)."""
    return f"budget:{scope_key}:{period_key}:open"


def _booked_key(scope_key: str, period_key: str) -> str:
    """Where the row as the latest booking left it in Postgres is kept, beside the counter."""
    return f"budget:{scope_key}:{period_key}:booked"


def _keys(scope_key: str, period_key: str) -> list[str]:
    return [
        _key(scope_key, period_key),
        _open_key(scope_key, period_key),
        _booked_key(scope_key, period_key),
    ]


#: A correction the store could not take: the script's keys and arguments, and when it was held.
type _Held = tuple[list[str], list[ScriptArg], float]


class BudgetLedger:
    """Atomic reserve / settle / release over the shared counter store."""

    def __init__(
        self, runner: ScriptRunner, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._runner = runner
        # Injectable so a held correction's age can be tested without waiting an hour.
        self._clock = clock
        #: Corrections the store could not take, oldest first (module docstring).
        self._held: deque[_Held] = deque()
        #: Held corrections sent, and held ones given way to the limit, since the store went away.
        self._resent = 0
        self._dropped = 0

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
        # What was held goes first: the decision below reads the counters it corrects. A store
        # that refuses one of those was never sent this reservation.
        try:
            await self._send_held()
        except CountersUnavailable as exc:
            if isinstance(exc, CountersNotAsked):
                raise
            raise CountersNotAsked(str(exc)) from exc
        now = int(at)
        deadline = now + RESERVATION_LIFETIME_SECONDS
        entry = f"{deadline}:{amounts.tokens}:{amounts.requests}:{amounts.cost_nanos}"
        breached = await self._runner.run(
            _RESERVE,
            _keys(scope_key, period_key),
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
        self,
        scope_key: str,
        period_key: str,
        *,
        amounts: Amounts,
        reservation: str = "",
        reserved: Amounts = _NOTHING,
        booked: Amounts | None = None,
    ) -> None:
        """Move a counter by ``amounts`` — a correction after settling, a release, or a figure no
        reservation held. Settling or releasing ``reservation`` also closes it, so no rebuild adds
        it back, and moves the counter only while it is open: a second resolution moves nothing.

        ``booked`` is the counter's row in Postgres as this booking left it, for a booking of at
        least one request. A counter whose basis already holds it takes back only ``reserved``,
        what the reservation set aside (module docstring). Raises
        :class:`~aira_common.counters.CountersUnavailable` if Redis cannot be reached.
        """
        await self._runner.run(
            _ADJUST,
            _keys(scope_key, period_key),
            _correction(amounts, reservation, reserved, booked),
        )

    async def resolve(
        self,
        scope_key: str,
        period_key: str,
        *,
        amounts: Amounts,
        reservation: str,
        reserved: Amounts,
        booked: Amounts | None = None,
    ) -> None:
        """Settle or release ``reservation`` on one counter: now, or once the store answers again.

        Never lost to an unreachable store, and never raises for one: a correction the store cannot
        take is held and sent before the next thing this ledger asks of it. One that reached the
        store although its reply did not, sent again, moves nothing (`adjust`).
        """
        keys = _keys(scope_key, period_key)
        arguments = _correction(amounts, reservation, reserved, booked)
        try:
            await self._send_held()
            await self._runner.run(_ADJUST, keys, arguments)
        except BaseException as exc:
            # Held whatever stopped it — the store away, or a cancellation that landed before or
            # after the script ran.
            self._hold(keys, arguments)
            if not isinstance(exc, CountersUnavailable):
                raise

    def _hold(self, keys: list[str], arguments: list[ScriptArg]) -> None:
        if len(self._held) >= HELD_CORRECTIONS_LIMIT:
            # The oldest gives way: its reservation is carried by every rebuild until its deadline.
            self._held.popleft()
            self._dropped += 1
            if self._dropped == 1:
                _log.warning("budget_corrections_dropped", limit=HELD_CORRECTIONS_LIMIT)
        self._held.append((keys, arguments, self._clock()))

    async def _send_held(self) -> None:
        """Send the corrections held for the store, oldest first, until it refuses one again."""
        sent = 0
        while self._held and sent < RESEND_PER_CALL:
            keys, arguments, since = self._held.popleft()
            if self._clock() - since > RESERVATION_LIFETIME_SECONDS:
                continue  # its reservation is past its deadline, which no rebuild carries
            try:
                await self._runner.run(_ADJUST, keys, arguments)
            except BaseException:
                self._held.appendleft((keys, arguments, since))
                raise
            sent += 1
            self._resent += 1
        if sent and not self._held:
            _log.info("budget_corrections_resent", resent=self._resent, dropped=self._dropped)
            self._resent = self._dropped = 0


def _correction(
    amounts: Amounts, reservation: str, reserved: Amounts, booked: Amounts | None
) -> list[ScriptArg]:
    """`_ADJUST`'s arguments: the movement, the counter's lifetime, the reservation it closes, what
    that reservation set aside (moved back alone where the basis holds the booking), and the row as
    the booking left it — empty where nothing was booked."""
    withdrawal = reserved.negated()
    row: list[ScriptArg] = (
        ["", "", ""] if booked is None else [booked.tokens, booked.requests, booked.cost_nanos]
    )
    return [
        amounts.tokens,
        amounts.requests,
        amounts.cost_nanos,
        COUNTER_TTL_SECONDS,
        reservation,
        withdrawal.tokens,
        withdrawal.requests,
        withdrawal.cost_nanos,
        *row,
    ]
