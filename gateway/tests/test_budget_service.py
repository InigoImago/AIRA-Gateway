from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
NEXT_DAY = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


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
        budgets = await service.guard("uc", "bob", NOW)
        await service.record(budgets, 10, NOW)

    with pytest.raises(BudgetExceeded, match="Request budget"):
        await service.guard("uc", "bob", NOW)


async def test_token_budget_blocks_once_exceeded(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_tokens=100)
    service = BudgetService(sessionmaker)

    budgets = await service.guard("uc", "bob", NOW)  # 0 < 100
    await service.record(budgets, 60, NOW)  # 60
    budgets = await service.guard("uc", "bob", NOW)  # 60 < 100 still allowed
    await service.record(budgets, 60, NOW)  # 120

    with pytest.raises(BudgetExceeded, match="Token budget"):
        await service.guard("uc", "bob", NOW)


async def test_member_scope_only_applies_to_subject(sessionmaker) -> None:
    await _add(sessionmaker, id=1, scope="member", subject="bob", limit_requests=1)
    service = BudgetService(sessionmaker)

    # alice is not the budget's subject → not applicable
    assert await service.guard("uc", "alice", NOW) == []

    budgets = await service.guard("uc", "bob", NOW)
    await service.record(budgets, 5, NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)


async def test_period_rolls_over(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record(await service.guard("uc", "bob", NOW), 1, NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)
    # next day → fresh counter
    assert await service.guard("uc", "bob", NEXT_DAY) != []


async def test_disabled_budget_is_ignored(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1, enabled=False)
    service = BudgetService(sessionmaker)
    assert await service.guard("uc", "bob", NOW) == []


async def test_enforce_off_returns_empty(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=False)
    assert await service.guard("uc", "bob", NOW) == []


async def test_no_use_case_returns_empty(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    assert await service.guard(None, "bob", NOW) == []


async def test_record_no_budgets_is_noop(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    await service.record([], 100, NOW)  # must not raise


async def test_month_period_key(sessionmaker) -> None:
    await _add(sessionmaker, id=1, period="month", limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record(await service.guard("uc", "bob", NOW), 1, NOW)
    # same month, next day → still counts (monthly window)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NEXT_DAY)
