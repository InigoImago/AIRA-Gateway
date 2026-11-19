"""A correction to a budget counter is never lost to an unreachable counter store.

`RedisRunner` stops asking Redis for a few seconds after any failed call — a single one-second
timeout is enough — so that an outage does not make every request wait for one. A correction made
in those seconds (a settlement, a release, the give-back of a half-made reservation) was refused
without being sent, and dropped. That left the reservation open beside its counter, and every
rebuild of the counter in the next hour added the request's estimate back on top of the figure
Postgres already held: a use case near its limit was refused as "held by requests still in flight"
with nothing in flight, while its own budget view showed room.

These tests run the real runner, breaker included; only its transport is a stand-in — an
in-process Redis behind a socket that times out. The stand-ins of the older tests recovered on the
very next call, which the real breaker does not, so they could not see this.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
import redis.exceptions
from sqlalchemy.ext.asyncio import async_sessionmaker

import aira_gateway.budgets.ledger as ledger_module
from aira_common.counters import (
    RETRY_AFTER_FAILURE_SECONDS,
    CountersUnavailable,
    DisabledRunner,
    RedisRunner,
)
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import (
    HELD_CORRECTIONS_LIMIT,
    RESERVATION_LIFETIME_SECONDS,
    Amounts,
    BudgetLedger,
)
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
COUNTER = "budget:uc:uc:2026-08"
OPEN = f"{COUNTER}:open"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Transport:
    """What the runner talks to: an in-process Redis behind a socket that can time out — for the
    calls in ``away`` before the script reaches the store, for those in ``lost`` after it ran and
    before its reply came back, as a one-second ``socket_timeout`` on a busy server does."""

    def __init__(self) -> None:
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.calls = 0
        self.away: set[int] = set()
        self.lost: set[int] = set()

    def register_script(self, script: str) -> Any:
        registered = self.redis.register_script(script)

        async def call(keys: Any, args: Any) -> Any:
            self.calls += 1
            if self.calls in self.away:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            result = await registered(keys=keys, args=args)
            if self.calls in self.lost:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            return result

        return call

    async def aclose(self) -> None:
        await self.redis.aclose()


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def store() -> AsyncIterator[tuple[RedisRunner, _Transport, _Clock]]:
    clock = _Clock()
    runner = RedisRunner("redis://unused:6379/0", clock=clock)
    transport = _Transport()
    runner._client = transport  # the transport only: `run` and its breaker are the real ones
    yield runner, transport, clock
    await transport.aclose()


async def _budget(sessionmaker: async_sessionmaker, **fields: Any) -> None:
    row = {"id": 1, "use_case": "uc", "scope": "use_case", "subject": "", "period": "month"}
    row.update(fields)
    async with sessionmaker() as session:
        session.add(BudgetRead(enabled=True, **row))
        await session.commit()


async def test_a_give_back_the_breaker_refused_reaches_the_counter_once_it_answers(
    sessionmaker, store
) -> None:
    """The first budget reserved, the second's call times out: the request is admitted on the
    Postgres path, and the first budget's reservation is handed back — through a runner whose
    breaker has just opened."""
    runner, transport, clock = store
    await _budget(sessionmaker, id=1, limit_requests=10)
    await _budget(sessionmaker, id=2, scope="each_member", limit_requests=10)
    transport.away = {2}
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    admitted = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    assert admitted.atomic is False

    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1  # the store answers again
    await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    counter = await transport.redis.hgetall(COUNTER)
    assert int(counter["requests"]) == 1, f"the handed-back reservation is still counted: {counter}"
    assert await transport.redis.hlen(OPEN) == 1, "only the request now in flight is open"


async def test_a_settlement_during_a_blip_does_not_keep_refusing_the_use_case(
    sessionmaker, store
) -> None:
    """One request in flight, one unrelated call that times out, and the request settles a second
    later — while the breaker is open. Ten minutes on the counter has lapsed and is rebuilt:
    Postgres holds 1 000 of 40 000 tokens and nothing is in flight."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_tokens=40_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    # An uncapped request reserves what bounds its answer: a model maximum of 50 000 tokens.
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=50_000, requests=1))
    assert first.atomic is True
    transport.away = {2}
    with pytest.raises(
        CountersUnavailable
    ):  # another feature's call — a rate limit's, say — times out
        await runner.run("return 1", ["x"], [])

    clock.now += 1
    await service.settle(first, 1_000, now=NOW + timedelta(seconds=1))
    async with sessionmaker() as session:
        usage = await session.get(BudgetUsage, ("uc:uc", "2026-08"))
    assert usage is not None and usage.tokens == 1_000

    clock.now += 600
    await transport.redis.delete(COUNTER)  # its lifetime is over; the next request rebuilds it
    try:
        await service.guard(
            "uc", "bob", NOW + timedelta(minutes=10), estimated=Amounts(tokens=1_000, requests=1)
        )
    except BudgetExceeded as refused:
        pytest.fail(
            "refused ten minutes after one timeout, with 1 000 of 40 000 tokens spent and nothing "
            f"in flight: {refused.message}"
        )
    assert int((await transport.redis.hgetall(COUNTER))["tokens"]) == 2_000


async def test_a_reservation_whose_reply_was_lost_is_handed_back(sessionmaker, store) -> None:
    """The script ran and its reply never came: the request is admitted on the Postgres path, and
    the reservation Redis did make must not stay behind with nobody to settle it."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_tokens=100_000)
    transport.lost = {1}
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))

    admitted = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=50_000))
    assert admitted.atomic is False

    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=1_000))
    counter = await transport.redis.hgetall(COUNTER)
    assert int(counter["tokens"]) == 1_000, f"a reservation nobody holds is counted: {counter}"
    assert await transport.redis.hlen(OPEN) == 1


async def test_a_correction_whose_reply_was_lost_moves_the_counter_once(
    sessionmaker, store
) -> None:
    """Held and sent again, a correction that had already reached the store finds its reservation
    closed and moves nothing: corrected twice, a settled request would hand back more than it held.
    """
    runner, transport, clock = store
    await _budget(sessionmaker, limit_tokens=100_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1))
    transport.lost = {2}  # the settlement's correction runs; its reply is lost

    await service.settle(first, 4_000, now=NOW)
    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))

    counter = await transport.redis.hgetall(COUNTER)
    assert (int(counter["tokens"]), int(counter["requests"])) == (4_000, 2), counter


async def test_a_held_correction_survives_a_failed_attempt_to_send_it(sessionmaker, store) -> None:
    """The store is still away when the next request tries to send what was held: the correction
    stays held, and lands once the store answers."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_tokens=100_000)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1))
    transport.away = {2, 3}  # the settlement's correction, and the next attempt to send it

    await service.settle(first, 4_000, now=NOW)
    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    admitted = await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))
    assert admitted.atomic is False, "the store was still away"
    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "carol", NOW, estimated=Amounts(tokens=0, requests=1))

    counter = await transport.redis.hgetall(COUNTER)
    assert (int(counter["tokens"]), int(counter["requests"])) == (4_000, 2), counter


async def test_a_request_the_store_was_not_asked_about_leaves_nothing_held(
    sessionmaker, store
) -> None:
    """While the breaker is open the store is not asked at all, so the requests it admits on the
    Postgres path reserved nothing there and have nothing to give back. Held anyway, each would add
    a give-back to the ledger — one per request for as long as the outage lasts, until the oldest,
    the corrections actually owed, gave way to them."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10**6)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner)))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=0, requests=1))
    transport.away = {2}
    await service.settle(first, 0, now=NOW)  # owed, and held: the breaker opens on it
    assert len(ledger._held) == 1

    for n in range(10):
        admitted = await service.guard("uc", f"p{n}", NOW, estimated=Amounts(requests=1))
        assert admitted.atomic is False, "the store is not asked while the breaker is open"
    assert len(ledger._held) == 1, "a request the store never saw was held as if it owed one"

    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))
    assert not ledger._held
    assert int((await transport.redis.hgetall(COUNTER))["requests"]) == 2


async def test_without_a_store_nothing_is_ever_held(sessionmaker) -> None:
    """An installation without Redis takes the Postgres path on every request, and nothing it does
    can owe a counter a correction: the ledger stays empty however many requests pass."""
    await _budget(sessionmaker, limit_requests=10**6)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(DisabledRunner())))
    for n in range(20):
        admitted = await service.guard("uc", f"p{n}", NOW, estimated=Amounts(requests=1))
        await service.settle(admitted, 0, now=NOW)
    assert not ledger._held


async def test_a_reservation_an_open_breaker_refused_unasked_is_not_held(
    sessionmaker, store
) -> None:
    """The breaker opened on a call that owed nothing back — a pipeline step's spend added to the
    counter — so nothing is held when the next request comes. Its reservation is refused by the
    breaker without the store being asked, which the runner says (`CountersNotAsked`): there is
    nothing to give back for it, and nothing is held."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10**6)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner)))
    transport.away = {1}
    await service.book_side_call("uc", "alice", 10, now=NOW)  # the call that opens the breaker
    assert not ledger._held

    admitted = await service.guard("uc", "bob", NOW, estimated=Amounts(requests=1))
    assert admitted.atomic is False
    assert not ledger._held, "a reservation the store was never asked about was held"


async def test_a_reservation_behind_a_held_correction_that_failed_again_is_not_held(
    sessionmaker, store
) -> None:
    """The store is asked again after its pause and fails on the first thing sent — what was held.
    The reservation behind it was never sent, although the failure was a real call's: the ledger
    says so, and only the correction that is owed stays held."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10**6)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner)))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=0, requests=1))
    transport.away = {2, 3}  # the settlement's correction, and the next attempt to send it
    await service.settle(first, 0, now=NOW)
    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1

    admitted = await service.guard("uc", "bob", NOW, estimated=Amounts(requests=1))
    assert admitted.atomic is False, "the store was still away"
    assert len(ledger._held) == 1, "the reservation behind the held correction was held too"


async def test_one_call_sends_a_bounded_share_of_what_was_held(
    sessionmaker, store, monkeypatch
) -> None:
    """The first request after an outage does not pay for all of it: each call sends a share
    before its own work, and the calls after it send the rest."""
    monkeypatch.setattr(ledger_module, "RESEND_PER_CALL", 1)
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner)))
    held = [
        await service.guard("uc", f"p{n}", NOW, estimated=Amounts(requests=1)) for n in range(2)
    ]
    transport.away = {3}
    for reservation in held:
        await service.release(reservation)
    assert len(ledger._held) == 2

    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "bob", NOW, estimated=Amounts(requests=1))
    assert len(ledger._held) == 1
    await service.guard("uc", "carol", NOW, estimated=Amounts(requests=1))
    assert not ledger._held
    assert int((await transport.redis.hgetall(COUNTER))["requests"]) == 2


async def test_held_corrections_are_bounded_and_the_oldest_gives_way(
    sessionmaker, store, monkeypatch
) -> None:
    """A process holds what the store cannot take — but no more than a bound, or an outage would
    grow it without end. Past it the oldest gives way, and its reservation is carried until its
    deadline, as before anything was held."""
    monkeypatch.setattr(ledger_module, "HELD_CORRECTIONS_LIMIT", 2)
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner)))
    held = [
        await service.guard("uc", f"p{n}", NOW, estimated=Amounts(requests=1)) for n in range(3)
    ]
    transport.away = {4}

    for reservation in held:
        await service.release(reservation)

    assert len(ledger._held) == 2
    clock.now += RETRY_AFTER_FAILURE_SECONDS + 1
    await service.guard("uc", "bob", NOW, estimated=Amounts(requests=1))
    # The first release gave way; the other two reached the counter, beside bob's reservation.
    assert int((await transport.redis.hgetall(COUNTER))["requests"]) == 2
    assert HELD_CORRECTIONS_LIMIT >= 1_000, "a bound below what one instance has in flight"


async def test_a_correction_held_past_its_reservations_lifetime_is_not_sent(
    sessionmaker, store
) -> None:
    """Past the reservation's deadline no rebuild carries it, so there is nothing left to correct —
    and a correction the store keeps refusing must not be retried for ever."""
    runner, transport, clock = store
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, ledger=(ledger := BudgetLedger(runner, clock=clock)))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    transport.away = {2}
    await service.release(first)
    assert len(ledger._held) == 1

    clock.now += RESERVATION_LIFETIME_SECONDS + 1
    sent = transport.calls
    await service.guard("uc", "bob", NOW, estimated=Amounts(requests=1))

    assert transport.calls == sent + 1, "the stale correction was sent before the reservation"
    assert not ledger._held
