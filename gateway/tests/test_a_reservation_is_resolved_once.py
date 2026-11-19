"""A reservation is resolved once: a second settlement or release does nothing.

`Reservation` promised it, and only the callers kept the promise — each checked `resolved` before
resolving. `settle` and `release` themselves moved the counter regardless, and so did the counter's
own script, which subtracted whether or not the reservation was still open. A second release took
the estimate out of *another* request's reservation while that one was still in flight, and the
counter's clamp at zero hid it from a test that only looked at the end state; a second settlement
booked the request twice. Held at both places, because a correction the store could not take is
sent again later — and it must then move nothing if it had arrived after all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger, Limits
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
COUNTER = "budget:uc:uc:2026-08"


class _Runner:
    """The ledger's scripts on an in-process Redis."""

    def __init__(self) -> None:
        self.redis = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        return await self.redis.register_script(script)(keys=list(keys), args=list(args))


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    async with build_sessionmaker(engine)() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                subject="",
                period="month",
                limit_requests=2,
                enabled=True,
            )
        )
        await session.commit()
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_a_second_release_does_not_free_another_requests_reservation(sessionmaker) -> None:
    runner = _Runner()
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))  # still in flight

    await service.release(first)
    await service.release(first)

    counter = await runner.redis.hgetall(COUNTER)
    assert int(counter["requests"]) == 1, f"the request in flight is no longer counted: {counter}"
    # What that would hand out: with one request in flight, a limit of 2 admits one more.
    await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))


async def test_a_second_settlement_books_nothing(sessionmaker) -> None:
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_Runner()))
    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))

    await service.settle(reservation, 40, cost_nanos=7, now=NOW)
    await service.settle(reservation, 40, cost_nanos=7, now=NOW)

    async with sessionmaker() as session:
        usage = await session.get(BudgetUsage, ("uc:uc", "2026-08"))
    assert usage is not None
    assert (usage.tokens, usage.requests, usage.cost_nanos) == (40, 1, 7)


async def test_a_reservation_closed_once_moves_its_counter_once() -> None:
    """The counter's own script, asked twice to give one reservation back — as it is when a
    correction whose reply was lost is sent again."""
    runner = _Runner()
    ledger = BudgetLedger(runner)
    for name in ("first", "second"):
        await ledger.reserve(
            "uc:uc",
            "2026-08",
            limits=Limits(requests=10),
            amounts=Amounts(tokens=500, requests=1),
            seed=Amounts(),
            reservation=name,
            at=NOW.timestamp(),
        )

    for _ in range(2):
        await ledger.adjust(
            "uc:uc", "2026-08", amounts=Amounts(tokens=-500, requests=-1), reservation="first"
        )

    counter = await runner.redis.hgetall(COUNTER)
    assert (int(counter["tokens"]), int(counter["requests"])) == (500, 1), counter
