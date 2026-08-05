"""Cost-based budgeting in the gateway (FRD-403)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.money import to_nanos
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService
from aira_gateway.consumer.apply import apply_event
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.base import Base
from aira_gateway.db.models import BudgetRead, ModelPriceRead
from aira_gateway.pricing import PricingService

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


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


async def _add(sessionmaker, *rows) -> None:
    async with sessionmaker() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _priced(model: str, input_price: str, output_price: str) -> ModelPriceRead:
    return ModelPriceRead(
        model=model,
        input_price_per_million_nanos=to_nanos(input_price),
        output_price_per_million_nanos=to_nanos(output_price),
    )


# ---- pricing ---------------------------------------------------------------------------


async def test_a_request_is_priced_by_direction(sessionmaker) -> None:
    await _add(sessionmaker, _priced("pro-1", "1.00", "10.00"))
    pricing = PricingService(sessionmaker)

    usage = CanonicalUsage(prompt_tokens=1_000_000, completion_tokens=100_000)
    assert await pricing.cost_nanos("pro-1", usage) == to_nanos("2.00")


async def test_an_unpriced_model_yields_none_not_zero(sessionmaker) -> None:
    """ "We do not know" and "it was free" must not be the same number."""
    await _add(sessionmaker, ModelPriceRead(model="mystery-1"))
    pricing = PricingService(sessionmaker)

    usage = CanonicalUsage(prompt_tokens=1000, completion_tokens=1000)
    assert await pricing.cost_nanos("mystery-1", usage) is None
    assert await pricing.cost_nanos("never-catalogued", usage) is None
    assert await pricing.cost_nanos("mystery-1", None) is None


async def test_a_half_priced_model_counts_as_unpriced(sessionmaker) -> None:
    # Billing one direction and ignoring the other would look complete and be wrong.
    await _add(
        sessionmaker,
        ModelPriceRead(model="half-1", input_price_per_million_nanos=to_nanos("1.00")),
    )
    assert await PricingService(sessionmaker).price_for("half-1") is None


# ---- enforcement -----------------------------------------------------------------------


async def test_a_cost_budget_blocks_once_the_limit_is_reached(sessionmaker) -> None:
    await _add(
        sessionmaker,
        BudgetRead(
            id=1,
            use_case="uc",
            scope="use_case",
            subject="",
            period="month",
            limit_cost_nanos=to_nanos("1.00"),
            enabled=True,
        ),
    )
    service = BudgetService(sessionmaker)

    budgets = (await service.guard("uc", "alice", NOW)).budgets
    await service.record(budgets, 1000, cost_nanos=to_nanos("0.60"), now=NOW)
    # Still under the limit.
    budgets = (await service.guard("uc", "alice", NOW)).budgets
    await service.record(budgets, 1000, cost_nanos=to_nanos("0.60"), now=NOW)

    with pytest.raises(BudgetExceeded, match="Cost budget exhausted"):
        await service.guard("uc", "alice", NOW)


async def test_unpriced_traffic_is_counted_apart_and_never_as_free(sessionmaker) -> None:
    await _add(
        sessionmaker,
        BudgetRead(
            id=1,
            use_case="uc",
            scope="use_case",
            subject="",
            period="month",
            limit_cost_nanos=to_nanos("1.00"),
            enabled=True,
        ),
    )
    service = BudgetService(sessionmaker)

    budgets = (await service.guard("uc", "alice", NOW)).budgets
    await service.record(budgets, 5000, cost_nanos=None, now=NOW)

    usage = (await service.usage("uc", NOW))[0]
    assert usage["used_cost_nanos"] == 0
    assert usage["used_cost"] == "0.00"  # a genuine zero, unlike a tiny amount
    assert usage["unpriced_requests"] == 1
    assert usage["used_tokens"] == 5000  # the volume is still visible
    # It cost something, we just cannot say what — so it must not consume the cost budget.
    await service.guard("uc", "alice", NOW)


async def test_usage_reports_cost_as_an_exact_string(sessionmaker) -> None:
    await _add(
        sessionmaker,
        BudgetRead(
            id=1,
            use_case="uc",
            scope="use_case",
            subject="",
            period="month",
            enabled=True,
            limit_cost_nanos=to_nanos("10.00"),
        ),
    )
    service = BudgetService(sessionmaker)
    budgets = (await service.guard("uc", "alice", NOW)).budgets
    for _ in range(3):
        await service.record(budgets, 100, cost_nanos=to_nanos("0.105"), now=NOW)

    usage = (await service.usage("uc", NOW))[0]
    assert usage["used_cost_nanos"] == to_nanos("0.315")
    assert usage["used_cost"] == "0.32"  # displayed rounded, stored exact


async def test_cost_and_count_limits_coexist(sessionmaker) -> None:
    await _add(
        sessionmaker,
        BudgetRead(
            id=1,
            use_case="uc",
            scope="use_case",
            subject="",
            period="day",
            limit_cost_nanos=to_nanos("100.00"),
            limit_requests=1,
            enabled=True,
        ),
    )
    service = BudgetService(sessionmaker)
    budgets = (await service.guard("uc", "alice", NOW)).budgets
    await service.record(budgets, 10, cost_nanos=to_nanos("0.01"), now=NOW)

    # Far below the cost limit, but the request count is spent.
    with pytest.raises(BudgetExceeded, match="Request budget exhausted"):
        await service.guard("uc", "alice", NOW)


# ---- distribution ----------------------------------------------------------------------


async def test_prices_arrive_from_the_catalog_as_exact_decimals(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {
                "name": "flash-1",
                "display_name": "Flash",
                "provider": "google",
                # Strings, not JSON numbers: a float round-trip would corrupt the price.
                "input_price_per_million": "0.075",
                "output_price_per_million": "0.30",
            },
        )

    price = await PricingService(sessionmaker).price_for("flash-1")
    assert price is not None
    assert price.input_per_million_nanos == to_nanos("0.075")
    assert price.output_per_million_nanos == to_nanos("0.30")


async def test_a_price_change_replaces_the_old_one(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {"name": "m", "input_price_per_million": "1.00", "output_price_per_million": "1.00"},
        )
        await apply_event(
            session,
            "model.upserted",
            {"name": "m", "input_price_per_million": "2.00", "output_price_per_million": "2.00"},
        )
    price = await PricingService(sessionmaker).price_for("m")
    assert price is not None and price.input_per_million_nanos == to_nanos("2.00")


async def test_removing_a_model_removes_its_price(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {"name": "m", "input_price_per_million": "1.00", "output_price_per_million": "1.00"},
        )
        await apply_event(session, "model.deleted", {"name": "m"})
    assert await PricingService(sessionmaker).price_for("m") is None


async def test_a_cost_limit_arrives_from_management(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "budget.upserted",
            {
                "id": 7,
                "use_case": "uc",
                "scope": "use_case",
                "subject": "",
                "period": "month",
                "limit_cost": "25.50",
                "limit_tokens": None,
                "limit_requests": None,
                "enabled": True,
            },
        )
        budget = await session.get(BudgetRead, 7)
    assert budget is not None
    assert budget.limit_cost_nanos == to_nanos("25.50")
