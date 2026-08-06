import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
NEXT_DAY = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _budgets(sessionmaker: async_sessionmaker) -> list[BudgetRead]:
    """The configured budgets, as `guard` would have resolved them."""
    async with sessionmaker() as session:
        return list((await session.execute(select(BudgetRead))).scalars())


async def _add(sessionmaker: async_sessionmaker, **fields: object) -> None:
    defaults = {
        "use_case": "uc",
        "scope": "use_case",
        "subject": "",
        "period": "day",
        "limit_tokens": None,
        "limit_requests": None,
        "enabled": True,
    }
    defaults.update(fields)
    async with sessionmaker() as session:
        session.add(BudgetRead(**defaults))  # type: ignore[arg-type]
        await session.commit()


async def test_request_budget_blocks_after_limit(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=2)
    service = BudgetService(sessionmaker)

    for _ in range(2):
        budgets = (await service.guard("uc", "bob", NOW)).budgets
        await service.record(budgets, 10, now=NOW)

    with pytest.raises(BudgetExceeded, match="Request budget"):
        await service.guard("uc", "bob", NOW)


async def test_token_budget_blocks_once_exceeded(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_tokens=100)
    service = BudgetService(sessionmaker)

    budgets = (await service.guard("uc", "bob", NOW)).budgets  # 0 < 100
    await service.record(budgets, 60, now=NOW)  # 60
    budgets = (await service.guard("uc", "bob", NOW)).budgets  # 60 < 100 still allowed
    await service.record(budgets, 60, now=NOW)  # 120

    with pytest.raises(BudgetExceeded, match="Token budget"):
        await service.guard("uc", "bob", NOW)


async def test_member_scope_only_applies_to_subject(sessionmaker) -> None:
    await _add(sessionmaker, id=1, scope="member", subject="bob", limit_requests=1)
    service = BudgetService(sessionmaker)

    # alice is not the budget's subject → not applicable
    assert (await service.guard("uc", "alice", NOW)).budgets == []

    budgets = (await service.guard("uc", "bob", NOW)).budgets
    await service.record(budgets, 5, now=NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)


async def test_period_rolls_over(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 1, now=NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)
    # next day → fresh counter
    assert (await service.guard("uc", "bob", NEXT_DAY)).budgets != []


async def test_disabled_budget_is_ignored(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1, enabled=False)
    service = BudgetService(sessionmaker)
    assert (await service.guard("uc", "bob", NOW)).budgets == []


async def test_enforce_off_returns_empty(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=False)
    assert (await service.guard("uc", "bob", NOW)).budgets == []


async def test_no_use_case_returns_empty(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    assert (await service.guard(None, "bob", NOW)).budgets == []


async def test_record_no_budgets_is_noop(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    await service.record([], 100, now=NOW)  # must not raise


async def test_month_period_key(sessionmaker) -> None:
    await _add(sessionmaker, id=1, period="month", limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 1, now=NOW)
    # same month, next day → still counts (monthly window)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NEXT_DAY)


async def test_usage_reports_current_period(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=5)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 30, now=NOW)
    assert await service.usage("uc", NOW) == [
        {
            "id": 1,
            "used_tokens": 30,
            "used_requests": 1,
            "used_cost_nanos": 0,
            "used_cost": "0.00",
            "unpriced_requests": 1,
        }
    ]


# == the counter is accumulated in the database, not in Python (found live) =======================


async def test_two_concurrent_first_writes_do_not_collide(sessionmaker) -> None:
    """Found by twenty concurrent requests against a fresh budget on the live stack.

    ``record`` read the counter, then inserted it when absent, then committed. Two requests
    arriving as the **first** of a period both found no row and both inserted one; the loser got a
    `UniqueViolation` that reached the caller as a **500** — for a request the gateway had already
    served and charged for.
    """
    await _add(sessionmaker, id=1, limit_requests=100)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await asyncio.gather(*[service.record(budgets, tokens=1, cost_nanos=10) for _ in range(8)])

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().all()
    assert len(usage) == 1, "the counter was written more than once for one scope+period"


async def test_no_increment_is_lost_when_writes_overlap(sessionmaker) -> None:
    """The quieter half of the same defect, and the one with no error attached.

    ``record.tokens += n`` reads the loaded value and writes an **absolute** new one, so two
    overlapping writes discard one increment. There is nothing to see: the counter that is meant
    to be the system of record simply drifts below the truth, in the direction that spends money,
    under exactly the load that makes a budget matter.
    """
    await _add(sessionmaker, id=1, limit_requests=1000)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await asyncio.gather(*[service.record(budgets, tokens=10, cost_nanos=5) for _ in range(20)])

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().one()
    assert usage.tokens == 200, f"increments were lost: {usage.tokens} instead of 200"
    assert usage.requests == 20
    assert usage.cost_nanos == 100


async def test_an_unpriced_request_is_still_counted_apart_by_the_upsert(sessionmaker) -> None:
    """The rule the rewrite had to preserve: unpriced is not free (`FRD-403`)."""
    await _add(sessionmaker, id=1, limit_requests=1000)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await service.record(budgets, tokens=5, cost_nanos=None)
    await service.record(budgets, tokens=5, cost_nanos=7)

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().one()
    assert (usage.cost_nanos, usage.unpriced_requests) == (7, 1)
