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
from aira_gateway.db.models import BudgetRead, ModelRead
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


def _priced(model: str, input_price: str, output_price: str) -> ModelRead:
    return ModelRead(
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
    await _add(sessionmaker, ModelRead(model="mystery-1"))
    pricing = PricingService(sessionmaker)

    usage = CanonicalUsage(prompt_tokens=1000, completion_tokens=1000)
    assert await pricing.cost_nanos("mystery-1", usage) is None
    assert await pricing.cost_nanos("never-catalogued", usage) is None
    assert await pricing.cost_nanos("mystery-1", None) is None


async def test_a_half_priced_model_counts_as_unpriced(sessionmaker) -> None:
    # Billing one direction and ignoring the other would look complete and be wrong.
    await _add(
        sessionmaker,
        ModelRead(model="half-1", input_price_per_million_nanos=to_nanos("1.00")),
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


async def test_a_price_above_a_prompt_length_arrives_with_the_model(sessionmaker) -> None:
    """`FRD-408`: the length and its rates travel on the model's event, as the ordinary ones do."""
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {
                "name": "pro-1",
                "input_price_per_million": "1.25",
                "output_price_per_million": "10",
                "long_context_threshold": 200000,
                "long_context_input_price_per_million": "2.5",
                "long_context_output_price_per_million": "15",
                "long_context_cached_input_price_per_million": "0.25",
                "long_context_cache_write_price_per_million": None,
            },
        )

    price = await PricingService(sessionmaker).price_for("pro-1")
    assert price is not None and price.long_context is not None
    assert price.long_context_threshold == 200_000
    assert price.long_context.input_per_million_nanos == to_nanos("2.5")
    assert price.long_context.output_per_million_nanos == to_nanos("15")
    assert price.long_context.cached_input_per_million_nanos == to_nanos("0.25")
    assert price.long_context.cache_write_per_million_nanos is None


@pytest.mark.parametrize("length", [None, 0, -5, True, "200000"])
async def test_a_length_that_is_not_a_count_of_tokens_is_no_second_set(
    sessionmaker, length: object
) -> None:
    """Absent from an older Management, or not a positive whole number: the ordinary rates at
    every length, never a step at a length nobody stated."""
    payload = {
        "name": "pro-1",
        "input_price_per_million": "1.25",
        "output_price_per_million": "10",
        "long_context_input_price_per_million": "2.5",
        "long_context_output_price_per_million": "15",
    }
    if length is not None:
        payload["long_context_threshold"] = length
    async with sessionmaker() as session:
        await apply_event(session, "model.upserted", payload)

    price = await PricingService(sessionmaker).price_for("pro-1")
    assert price is not None
    assert price.long_context is None
    assert price.rates_for(10_000_000) is price


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


async def test_a_model_published_without_prices_stays_unpriced(sessionmaker) -> None:
    """ "Unknown is not zero" has to survive the distribution path, not just the pricing call.

    A model catalogued without prices arrives as a `model.upserted` event with two nulls. Turning
    those into 0 on the way in would make every request on that model cost exactly nothing —
    a complete-looking spend figure with a silent hole in it, which is the failure FRD-403 §4.4
    exists to prevent. Nothing was checking the conversion.
    """
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {"name": "unpriced-1", "display_name": "Unpriced", "provider": "mock"},
        )

    service = PricingService(sessionmaker)
    assert await service.price_for("unpriced-1") is None
    assert (
        await service.cost_nanos(
            "unpriced-1", CanonicalUsage(prompt_tokens=1000, completion_tokens=1000)
        )
        is None
    )


async def test_a_model_published_with_one_price_missing_stays_unpriced(sessionmaker) -> None:
    """Management refuses a half-priced model, but the gateway must not depend on that: a
    figure it cannot compute in full is unknown, never partially billed."""
    async with sessionmaker() as session:
        await apply_event(
            session,
            "model.upserted",
            {"name": "half-1", "input_price_per_million": "0.075"},
        )

    assert await PricingService(sessionmaker).price_for("half-1") is None


# ---- what the reservation prices ---------------------------------------------------------------


def _estimating_request(sessionmaker):  # noqa: ANN001, ANN202
    """The smallest stand-in `estimate` reads: settings, the catalogue and the price list."""
    from types import SimpleNamespace

    from aira_gateway.catalog import ModelCatalog
    from aira_gateway.config import GatewaySettings

    state = SimpleNamespace(
        settings=GatewaySettings(budget_estimate_output_tokens=1024),
        catalog=ModelCatalog(sessionmaker),
        pricing=PricingService(sessionmaker),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace())


async def test_an_embedding_reserves_what_its_input_costs(sessionmaker) -> None:
    """An embedding model is priced on input alone. Every reserved token priced at the output
    rate set aside **nothing** against a cost limit for it, so concurrent batches all passed the
    check: each side is priced at its own rate."""
    from aira_common.money import cost_nanos
    from aira_gateway.api.serving.controls import estimate

    await _add(sessionmaker, _priced("emb-1", "0.15", "0"))
    request = _estimating_request(sessionmaker)

    texts = ["x" * 4000] * 256
    expected = await estimate(
        request, model="emb-1", max_output_tokens=None, units=256, input_texts=texts
    )

    assert expected.cost_nanos > 0
    assert expected.cost_nanos == cost_nanos(256_000, to_nanos("0.15"))


async def test_a_reservation_is_never_negative(sessionmaker) -> None:
    """A negative figure on the way in — a resolved budget, a declaration — moved the shared
    counter down, and every request beside it then passed its check."""
    from aira_gateway.api.serving.controls import estimate

    await _add(sessionmaker, _priced("gen-1", "1.00", "2.00"))
    request = _estimating_request(sessionmaker)

    expected = await estimate(
        request, model="gen-1", max_output_tokens=None, extra_tokens=-1_000_000
    )

    assert expected.tokens >= 0
    assert expected.cost_nanos >= 0


async def test_a_long_prompt_reserves_at_the_rates_it_will_be_billed_at(sessionmaker) -> None:
    """`FRD-408`: a prompt past the model's stated length is billed at the second set, so it is
    reserved at it. Reserved at the ordinary rates, a session of 250 000-token turns held back half
    of what each turn then cost, and the requests in flight passed a limit together."""
    from aira_common.money import cost_nanos
    from aira_gateway.api.serving.controls import estimate

    row = _priced("pro-1", "1.25", "10")
    row.long_context_threshold = 200_000
    row.long_context_input_price_per_million_nanos = to_nanos("2.5")
    row.long_context_output_price_per_million_nanos = to_nanos("15")
    await _add(sessionmaker, row)
    request = _estimating_request(sessionmaker)

    # Four characters a token: 1 000 000 characters is a 250 000-token prompt.
    long = await estimate(
        request, model="pro-1", max_output_tokens=100, input_texts=["x" * 1_000_000]
    )
    short = await estimate(request, model="pro-1", max_output_tokens=100, input_texts=["x" * 4_000])

    assert long.cost_nanos == cost_nanos(long.tokens - 100, to_nanos("2.5")) + cost_nanos(
        100, to_nanos("15")
    )
    assert short.cost_nanos == cost_nanos(short.tokens - 100, to_nanos("1.25")) + cost_nanos(
        100, to_nanos("10")
    )
