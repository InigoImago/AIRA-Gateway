"""A lifted budget refuses nothing and still counts (`store.applicable`).

The console offers to *"lift a budget without losing it, or put it back"*, and the installation's
own switch (`AIRA_ENFORCE_BUDGETS=false`) keeps every figure true so that *"a limit switched back on
starts from what was actually spent"*. The per-budget switch did the opposite: `applicable` read
only enabled rows, so nothing a request spent while its budget was lifted was booked anywhere.
Measured on a request budget of 2: four requests served while it was lifted, put back, and the next
request was served again, reported as the first of the month — the lift had handed out a fresh
allowance for the rest of the period, and the figures said nothing had happened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


class _Runner:
    """The ledger's scripts on an in-process Redis — the store the guard reads first."""

    def __init__(self) -> None:
        self.client = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        return await self.client.register_script(script)(keys=list(keys), args=list(args))


async def _budget(sessionmaker: async_sessionmaker, *, enabled: bool, **limits: Any) -> None:
    async with sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                subject="",
                period="month",
                enabled=enabled,
                **limits,
            )
        )
        await session.commit()


async def _switch(sessionmaker: async_sessionmaker, *, enabled: bool) -> None:
    async with sessionmaker() as session:
        await session.execute(update(BudgetRead).values(enabled=enabled))
        await session.commit()


async def _serve(service: BudgetService, times: int = 1) -> None:
    """``times`` requests, each admitted and settled as the request path does."""
    for _ in range(times):
        reservation = await service.guard(
            "uc", "bob", NOW, estimated=Amounts(tokens=10, requests=1)
        )
        await service.settle(reservation, 10, now=NOW)


async def _used_requests(service: BudgetService) -> int:
    (row,) = await service.usage("uc", NOW)
    return int(row["used_requests"])


@pytest.mark.parametrize("ledger", [False, True], ids=["postgres", "shared counter"])
async def test_a_lifted_budget_refuses_nothing_and_counts_everything(
    sessionmaker: async_sessionmaker, ledger: bool
) -> None:
    await _budget(sessionmaker, enabled=False, limit_requests=2)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_Runner()) if ledger else None)

    await _serve(service, 4)  # twice the limit, and nothing refused

    assert await _used_requests(service) == 4


@pytest.mark.parametrize("ledger", [False, True], ids=["postgres", "shared counter"])
async def test_putting_it_back_caps_spending_from_what_was_spent(
    sessionmaker: async_sessionmaker, ledger: bool
) -> None:
    """The case measured: one request, lifted, three more, put back — and the next is refused,
    because four were spent against a limit of two. With the shared counter as well: it was built
    by the first request and outlives the lift, so it has to have been told about the three."""
    await _budget(sessionmaker, enabled=True, limit_requests=2)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_Runner()) if ledger else None)

    await _serve(service)
    await _switch(sessionmaker, enabled=False)
    await _serve(service, 3)
    await _switch(sessionmaker, enabled=True)

    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=10, requests=1))
    assert await _used_requests(service) == 4


async def test_a_lifted_budget_is_never_reserved_against(sessionmaker: async_sessionmaker) -> None:
    """Counting is not enforcing: nothing is set aside, so nothing can refuse."""
    await _budget(sessionmaker, enabled=False, limit_requests=1)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_Runner()))

    reservation = await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=10, requests=1))

    assert reservation.budgets == []
    assert [budget.id for budget in reservation.lifted] == [1]
    assert reservation.reserved == Amounts(tokens=10, requests=1)


async def test_a_pipeline_call_is_counted_against_a_lifted_budget_too(
    sessionmaker: async_sessionmaker,
) -> None:
    await _budget(sessionmaker, enabled=False, limit_requests=1)
    service = BudgetService(sessionmaker)

    await service.book_side_call("uc", "bob", 32, now=NOW)

    assert await _used_requests(service) == 1


async def test_a_lifted_budget_does_not_refuse_an_exhausted_use_case_early(
    sessionmaker: async_sessionmaker,
) -> None:
    """`refuse_if_exhausted`, the check before the pipeline, asks the same question."""
    await _budget(sessionmaker, enabled=False, limit_requests=1)
    service = BudgetService(sessionmaker)
    await _serve(service, 2)

    await service.refuse_if_exhausted("uc", "bob", NOW)  # does not raise


async def test_a_lifted_cost_budget_binds_no_model_to_a_price(
    sessionmaker: async_sessionmaker,
) -> None:
    """`FRD-610` §3.2 refuses an unpriced model under a cost limit; a lifted one limits nothing."""
    await _budget(sessionmaker, enabled=False, limit_cost_nanos=5_000_000_000)
    service = BudgetService(sessionmaker)

    assert await service.cost_limited("uc", "bob") is False
