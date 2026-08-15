"""Atomic budget reservation (FRD-405 §4.2).

These tests run the **real Lua scripts** against ``fakeredis`` rather than a hand-written stand-in
for them. The defect being fixed lives precisely in the gap between checking and booking, so a
fake that reimplements that logic in Python would be testing the wrong thing entirely.

``test_concurrent_requests_cannot_overshoot_a_budget`` is the one that matters: it fails against
the pre-FRD-405 implementation, which is what makes it evidence rather than decoration.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import fakeredis.aioredis as fakeredis
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.counters import CountersUnavailable
from aira_common.money import to_nanos
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import (
    COUNTER_TTL_SECONDS,
    Amounts,
    BudgetLedger,
    Limits,
)
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import Base
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class FakeRunner:
    """Runs scripts against an in-process Redis that supports Lua."""

    def __init__(self) -> None:
        self._client = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        registered = self._client.register_script(script)
        return await registered(keys=list(keys), args=list(args))

    async def close(self) -> None:
        await self._client.aclose()


class BrokenRunner:
    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        raise CountersUnavailable("down")

    async def close(self) -> None:
        return None


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def runner():
    runner = FakeRunner()
    yield runner
    await runner.close()


async def _budget(sessionmaker, **fields) -> None:
    defaults = {
        "id": 1,
        "use_case": "uc",
        "scope": "use_case",
        "subject": "",
        "period": "month",
        "enabled": True,
    }
    defaults.update(fields)
    async with sessionmaker() as session:
        session.add(BudgetRead(**defaults))
        await session.commit()


async def _usage(sessionmaker, scope_key="uc:uc", period_key="2026-08") -> BudgetUsage | None:
    async with sessionmaker() as session:
        return await session.get(BudgetUsage, (scope_key, period_key))


# ---- the race ----------------------------------------------------------------------------


async def test_concurrent_requests_cannot_overshoot_a_budget(sessionmaker, runner) -> None:
    """The defect this feature exists for: 20 requests in flight against room for one.

    Before the reservation, all 20 read a usage figure that none of them had booked yet and all
    20 passed. This test fails against that implementation.
    """
    await _budget(sessionmaker, limit_requests=1)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    async def attempt() -> bool:
        try:
            await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
        except BudgetExceeded:
            return False
        return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(20)))

    assert sum(outcomes) == 1


async def test_the_old_path_is_the_one_that_overshoots(sessionmaker) -> None:
    """Pins down *why* the shared counter is needed, so the fallback's weakness is documented
    rather than mistaken for equivalence."""
    await _budget(sessionmaker, limit_requests=1)
    service = BudgetService(sessionmaker, ledger=None)  # pre-FRD-405 behaviour

    async def attempt() -> bool:
        try:
            await service.guard("uc", "alice", NOW)
        except BudgetExceeded:
            return False
        return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(20)))

    assert sum(outcomes) == 20  # every one of them passes


async def test_a_concurrent_cost_budget_is_not_overshot(sessionmaker, runner) -> None:
    await _budget(sessionmaker, limit_cost_nanos=to_nanos("1.00"))
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    estimate = Amounts(tokens=1000, requests=1, cost_nanos=to_nanos("0.60"))

    async def attempt() -> bool:
        try:
            await service.guard("uc", "alice", NOW, estimated=estimate)
        except BudgetExceeded:
            return False
        return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(10)))

    # The first reserves 0.60 and the rest see it, so only one more can fit under 1.00.
    assert sum(outcomes) == 2


# ---- settling and releasing --------------------------------------------------------------


async def test_settling_books_the_actual_figure_to_postgres(sessionmaker, runner) -> None:
    await _budget(sessionmaker, limit_tokens=10_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    reservation = await service.guard(
        "uc", "alice", NOW, estimated=Amounts(tokens=1000, requests=1, cost_nanos=1000)
    )
    await service.settle(reservation, 42, cost_nanos=77, now=NOW)

    usage = await _usage(sessionmaker)
    assert usage is not None
    assert (usage.tokens, usage.requests, usage.cost_nanos) == (42, 1, 77)


async def test_the_reservation_is_corrected_to_the_actual_not_added_to_it(
    sessionmaker, runner
) -> None:
    """An over-estimate must not linger: it would consume headroom nobody ever used."""
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    reservation = await service.guard(
        "uc", "alice", NOW, estimated=Amounts(tokens=5000, requests=1, cost_nanos=9999)
    )
    await service.settle(reservation, 10, cost_nanos=20, now=NOW)

    counter = await runner._client.hgetall("budget:uc:uc:2026-08")
    assert int(counter["tokens"]) == 10
    assert int(counter["cost"]) == 20
    assert int(counter["requests"]) == 1


async def test_a_failed_request_releases_its_reservation(sessionmaker, runner) -> None:
    """Otherwise an upstream outage would look to a use case exactly like having spent its
    month: the budget would be consumed by requests that produced nothing."""
    await _budget(sessionmaker, limit_requests=2)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    for _ in range(5):
        reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
        await service.release(reservation)

    # Nothing was ever consumed, so the budget is still untouched.
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    assert reservation.budgets != []
    assert await _usage(sessionmaker) is None


async def test_a_budget_that_refuses_releases_what_this_request_already_reserved(
    sessionmaker, runner
) -> None:
    """With two budgets, the first may pass and the second refuse. Leaving the first
    reservation in place would let a refused request permanently consume headroom."""
    await _budget(sessionmaker, id=1, limit_requests=100)
    await _budget(sessionmaker, id=2, scope="each_member", limit_requests=1)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))  # consumes the member
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    # The wide use-case budget saw one successful reservation, not two.
    counter = await runner._client.hgetall("budget:uc:uc:2026-08")
    assert int(counter["requests"]) == 1


async def test_a_counter_never_goes_negative(sessionmaker, runner) -> None:
    """A double release, or a correction larger than what was reserved, must not drive a counter
    below zero — that would silently grant free headroom for the rest of the period instead of
    failing visibly. The clamp existed but nothing exercised it."""
    ledger = BudgetLedger(runner)
    key = "clamp-probe"

    await ledger.reserve(
        key,
        "2026-08",
        limits=Limits(requests=10),
        amounts=Amounts(tokens=5, requests=1, cost_nanos=5),
        seed=Amounts(),
    )
    # Give back far more than was ever taken.
    await ledger.adjust(key, "2026-08", amounts=Amounts(tokens=-99, requests=-99, cost_nanos=-99))

    counter = await runner._client.hgetall(f"budget:{key}:2026-08")
    assert {int(counter[field]) for field in ("tokens", "requests", "cost")} == {0}


async def test_a_double_release_does_not_hand_back_more_than_was_taken(
    sessionmaker, runner
) -> None:
    await _budget(sessionmaker, limit_requests=2)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    first = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    second = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    await service.release(first)
    await service.release(first)  # released twice by mistake
    await service.release(second)

    counter = await runner._client.hgetall("budget:uc:uc:2026-08")
    assert int(counter["requests"]) == 0  # not negative, which would be free headroom


# ---- Redis as a cache, Postgres as the record --------------------------------------------


async def test_the_counter_is_seeded_from_postgres_on_a_miss(sessionmaker, runner) -> None:
    """A Redis restart must not hand a use case its month back."""
    await _budget(sessionmaker, limit_requests=5)
    async with sessionmaker() as session:
        session.add(
            BudgetUsage(scope_key="uc:uc", period_key="2026-08", tokens=0, requests=5, cost_nanos=0)
        )
        await session.commit()

    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))


async def test_an_unreachable_redis_still_enforces_via_postgres(sessionmaker) -> None:
    """FRD-405 §4.3: falling back is enforcing-but-racy, not free spend."""
    await _budget(sessionmaker, limit_requests=1)
    async with sessionmaker() as session:
        session.add(
            BudgetUsage(scope_key="uc:uc", period_key="2026-08", tokens=0, requests=1, cost_nanos=0)
        )
        await session.commit()

    service = BudgetService(sessionmaker, ledger=BudgetLedger(BrokenRunner()))
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))


async def test_a_degraded_reservation_is_marked_so_settling_does_not_touch_redis(
    sessionmaker,
) -> None:
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(BrokenRunner()))

    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    assert reservation.atomic is False
    await service.settle(reservation, 5, cost_nanos=1, now=NOW)  # must not raise
    usage = await _usage(sessionmaker)
    assert usage is not None and usage.tokens == 5


async def test_redis_failing_between_reserve_and_settle_still_books_postgres(
    sessionmaker, runner
) -> None:
    """The authoritative figure must land even if the cache disappears mid-request."""
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    service._ledger = BudgetLedger(BrokenRunner())
    await service.settle(reservation, 33, cost_nanos=44, now=NOW)

    usage = await _usage(sessionmaker)
    assert usage is not None and (usage.tokens, usage.cost_nanos) == (33, 44)


class FlakyRunner:
    """Works for ``ok_calls``, then fails, then works again after ``recover_after`` failures.

    Models the case the fallback is written for: Redis is not gone for good, it is gone for the
    moment — which is exactly when a half-made reservation has to be handed back.
    """

    def __init__(self, inner: FakeRunner, ok_calls: int, recover_after: int = 10**9) -> None:
        self._inner = inner
        self._remaining = ok_calls
        self._failures = 0
        self._recover_after = recover_after

    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        if self._remaining <= 0 and self._failures < self._recover_after:
            self._failures += 1
            raise CountersUnavailable("down")
        self._remaining = max(self._remaining - 1, 0)
        return await self._inner.run(script, keys, args)

    async def close(self) -> None:
        await self._inner.close()


async def test_redis_failing_between_two_budgets_gives_back_what_it_already_took(
    sessionmaker, runner
) -> None:
    """Two applicable budgets is the ordinary case — a use-case budget plus a member one.

    If the first reservation succeeds and Redis then disappears, the request falls back to the
    Postgres path. The reservation already made must be handed back rather than becoming
    unreachable, because nothing downstream holds a reference to it any more.
    """
    await _budget(sessionmaker, id=1, limit_requests=10)
    await _budget(sessionmaker, id=2, scope="each_member", limit_requests=10)
    # Reserve on the first budget, fail on the second, then let the rollback through.
    flaky = FlakyRunner(runner, ok_calls=1, recover_after=1)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(flaky))

    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    assert reservation.atomic is False  # admitted by the fallback path
    counter = await runner._client.hgetall("budget:uc:uc:2026-08")
    assert int(counter.get("requests", 0)) == 0, f"the first reservation was left behind: {counter}"


async def test_a_counter_expires_long_before_its_budget_period_does(sessionmaker, runner) -> None:
    """The self-healing property, and the reason the fix is a short lifetime rather than a repair.

    A correction that cannot reach Redis cannot be repaired from Redis either — the store holding
    the stale figure is the store that is unreachable. What bounds the damage is that the counter
    is rebuilt from Postgres regularly, so a drifted figure cannot survive to the end of the
    month and refuse traffic Postgres says has room for.
    """
    await _budget(sessionmaker, limit_cost_nanos=to_nanos("100.00"))
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    await service.guard(
        "uc", "alice", NOW, estimated=Amounts(tokens=5000, requests=1, cost_nanos=to_nanos("9.00"))
    )

    ttl = await runner._client.ttl("budget:uc:uc:2026-08")
    assert 0 < ttl <= COUNTER_TTL_SECONDS
    assert COUNTER_TTL_SECONDS < 24 * 3600, "a day-long counter would outlive a daily budget"


async def test_a_rebuilt_counter_takes_the_settled_figure_from_postgres(
    sessionmaker, runner
) -> None:
    """What makes the short lifetime safe: the rebuild is not a reset."""
    await _budget(sessionmaker, limit_requests=5)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    await service.settle(reservation, 10, cost_nanos=20, now=NOW)

    await runner._client.delete("budget:uc:uc:2026-08")  # the counter expires

    await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    counter = await runner._client.hgetall("budget:uc:uc:2026-08")
    assert int(counter["requests"]) == 2  # the settled one from Postgres, plus this reservation


# ---- unchanged behaviour ------------------------------------------------------------------


async def test_a_use_case_without_budgets_reserves_nothing(sessionmaker, runner) -> None:
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    assert reservation.budgets == []
    assert bool(reservation) is False
    await service.settle(reservation, 10, now=NOW)  # no-op
    assert await _usage(sessionmaker) is None


# ---- what a pipeline step spends (FRD-125b) ----------------------------------------------


async def test_a_side_call_is_visible_to_the_very_next_guard(sessionmaker, runner) -> None:
    """**Enforcement, not just reporting.**

    The first version of `book_side_call` wrote Postgres alone. That is the system of record, so
    reporting was right — and the guard reads the *shared counter*, which never saw the spend until
    it expired and rebuilt, up to `COUNTER_TTL_SECONDS` later. Found live by setting a small cost
    cap and watching a use case sail past it: the counter said 41 000 against a limit of 40 000 and
    the next request was served anyway.
    """
    await _budget(sessionmaker, limit_cost_nanos=1_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    # **Warm the counter first.** Without this the test proves nothing: on a cold counter the guard
    # seeds it from Postgres and therefore sees a Postgres-only write anyway — which is exactly why
    # the first version of this test passed against the code it was written to fail against. Live,
    # the counter is warm by the second request, and that is when the spend goes missing.
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    await service.settle(reservation, 1, cost_nanos=1)

    await service.book_side_call("uc", "alice", tokens=10, cost_nanos=1_000, now=NOW)

    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))


async def test_a_side_call_reaches_the_system_of_record_as_well(sessionmaker, runner) -> None:
    """Both stores. The counter expires; Postgres is what the reporting and the rebuild read."""
    await _budget(sessionmaker, limit_cost_nanos=1_000_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    await service.book_side_call("uc", "alice", tokens=42, cost_nanos=500, now=NOW)

    usage = await _usage(sessionmaker, "uc:uc", "2026-08")
    assert usage is not None
    assert usage.tokens == 42
    assert usage.cost_nanos == 500
    # **One**, since 2026-08-15: the owner's decision to count a step's model call against the
    # request allowance, because it reaches a model and costs money. `FRD-125` FR-9's warning —
    # that this can trip a request limit for traffic the caller never sent — is accepted for
    # budgets and refused for rate limits, which still count arrivals.
    assert usage.requests == 1


async def test_a_side_call_with_an_unreachable_counter_still_reaches_postgres(
    sessionmaker,
) -> None:
    """The safe direction: the counter is *low*, so a caller is under-charged rather than refused
    for spend that never happened — and the counter rebuilds from Postgres when it expires."""
    service = BudgetService(sessionmaker, ledger=BudgetLedger(BrokenRunner()))
    await _budget(sessionmaker, limit_cost_nanos=1_000_000)

    await service.book_side_call("uc", "alice", tokens=7, cost_nanos=100, now=NOW)

    usage = await _usage(sessionmaker, "uc:uc", "2026-08")
    assert usage is not None and usage.tokens == 7


async def test_nothing_is_booked_for_a_request_with_no_use_case(sessionmaker, runner) -> None:
    """Unattributed traffic has no budget to charge, and inventing one would put somebody else's
    spend on a use case that did not ask for it."""
    await _budget(sessionmaker, limit_cost_nanos=1_000_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    await service.book_side_call(None, "alice", tokens=99, cost_nanos=999, now=NOW)

    assert await _usage(sessionmaker, "uc:uc", "2026-08") is None
