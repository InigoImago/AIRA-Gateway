"""A figure a caller writes must not be able to disable the control that reads it (2026-09-08).

The pre-dispatch reservation is what makes requests in flight visible to each other's check
(`FRD-405` §4.2) — the whole reason budgets are not racy. It moves the shared counter with
Redis' `HINCRBY`, which is **64-bit integer arithmetic**: at 2⁶³ Redis answers *"increment would
overflow"*, `RedisRunner` reports every such error as `CountersUnavailable` — which is exactly
what a Redis that is genuinely away looks like — and `BudgetService.guard` does the sensible thing
for that: it releases, logs, marks **budget enforcement degraded**, falls back to the racy
read-then-book path, and serves the request.

Measured, through the production runner over `fakeredis`:

    tokens = 1 000                atomic=True   degraded=False
    tokens = 2⁶²                  atomic=True   degraded=False
    tokens = 2⁶³                  atomic=False  degraded=True
    tokens = 10³⁰                 atomic=False  degraded=True

Two caller-named fields reach that arithmetic — `maxOutputTokens` and a `limited` thinking budget
— and **both were bounded only where the model declared a bound.** `max_output_tokens` and
`thinking_bounds` are nullable and default to `None`, so an ordinary catalogue row declares
neither and the field was bounded by nothing. That is `LESSONS.md` §1's *a value nobody wrote is a
value nothing checks*, applied to a **bound**: the check reads as present, and it is conditional
on a declaration nobody has to make.

The consequence is not a crash and not an overspend — the fallback still enforces against
Postgres. It is that a caller can choose, per request, which of two enforcement paths applies to
them, and leave `/readyz` blaming the counter store for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.counters import RedisRunner
from aira_gateway.app import create_app
from aira_gateway.budgets.ledger import Amounts, BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import BudgetRead, BudgetUsage, ModelRead, UseCaseRead

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
GEMINI = "/v1beta/models/mock-1:generateContent"


# =================================================================================================
# The bound is derived, so the derivation is what is asserted
# =================================================================================================


def test_the_ceiling_is_what_a_token_budget_can_hold() -> None:
    """`MAX_ACCOUNTABLE_TOKENS` is not a number somebody liked; it is the width of the column.

    Asserted against the column rather than against `2**31 - 1`, so that widening `budget_usage`
    to a `BigInteger` fails here and asks the question rather than leaving a ceiling that no longer
    means what its comment says. *A ceiling nobody can account for is a number somebody raises*
    (`LESSONS.md` §3), and this is the accounting.
    """
    tokens = BudgetUsage.__table__.c.tokens
    limit = BudgetRead.__table__.c.limit_tokens

    assert isinstance(tokens.type, Integer), tokens.type
    assert isinstance(limit.type, Integer), limit.type
    assert MAX_ACCOUNTABLE_TOKENS == 2**31 - 1


# =================================================================================================
# The ledger, driven through the production runner
# =================================================================================================


@pytest.fixture
async def sessionmaker() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _runner() -> RedisRunner:
    """`RedisRunner` itself, over `fakeredis`.

    **Not a stand-in for it.** The property under test is what happens to `guard` when the counter
    store raises, and that mapping — every exception becomes `CountersUnavailable` — lives in
    `RedisRunner`. A double that raised the Redis error directly would show a `500` where
    production shows a silent degradation, which is the wrong defect (`LESSONS.md` §7: *a stand-in
    more permissive than the thing it replaces proves the permission, not the rule*).
    """
    runner = RedisRunner("redis://unused-in-this-test")
    runner._client = fakeredis.FakeRedis(decode_responses=True)
    return runner


async def _budget(sessionmaker: Any) -> None:
    async with sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                period="day",
                limit_requests=1000,
                enabled=True,
            )
        )
        await session.commit()


@pytest.mark.parametrize("tokens", [1_000, MAX_ACCOUNTABLE_TOKENS, 2**62])
async def test_an_accountable_estimate_reserves_atomically(sessionmaker: Any, tokens: int) -> None:
    """The comparison half. A bound that degraded everything would pass the test below."""
    await _budget(sessionmaker)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_runner()))

    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens, 1))

    assert reservation.atomic is True
    assert not service._degradation.features


@pytest.mark.parametrize("tokens", [2**63, 10**30])
async def test_an_estimate_the_counter_cannot_hold_degrades_the_control(
    sessionmaker: Any, tokens: int
) -> None:
    """What the ceiling exists to make unreachable, pinned as the behaviour it *is*.

    `guard` is not wrong to fall back here — a counter store that raises is a counter store that
    is away, as far as this layer can tell. What must not happen is a **caller** being able to put
    it in that state, which is why the bound is at the door and not here.
    """
    await _budget(sessionmaker)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_runner()))

    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens, 1))

    assert reservation.atomic is False
    assert "budget enforcement" in service._degradation.features


# =================================================================================================
# The two doors, at the surface
# =================================================================================================


def _app() -> Any:
    return create_app(
        GatewaySettings(auth_required=False, environment="local", demo_mode=True, log_queue_size=0)
    )


async def _catalogue(app: Any) -> None:
    """A model that declares **no** output cap and **no** thinking bounds.

    Both columns are nullable and default to `None`, so this is the ordinary shape of a row
    somebody catalogues from the console — and it is the shape under which both checks were
    conditional on something that was not there.
    """
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc"))
        session.add(
            ModelRead(
                model="mock-1",
                numeric_id=9001,
                capabilities=["generate", "thinking"],
                approved=True,
                # `modes` and nothing else: no `min_tokens`, no `max_tokens`. That is the shape
                # under which `_limited_budget` had no ceiling to compare against — a declaration
                # somebody makes in one tick of a box.
                thinking={"modes": ["limited"]},
            )
        )
        await session.commit()


def _post(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post(GEMINI, json=body, headers={"x-aira-use-case": "uc"})


@pytest.mark.parametrize("requested", [2**63, 10**30])
async def test_max_output_tokens_above_the_ceiling_is_refused_by_name(requested: int) -> None:
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = _post(
            client,
            {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": requested},
            },
        )

    assert response.status_code == 400, response.text
    assert "this gateway can account for" in response.text


@pytest.mark.parametrize("requested", [2**63, 10**30])
async def test_a_thinking_budget_above_the_ceiling_is_refused_by_name(requested: int) -> None:
    """The second door. It is a different field, in a different module, checked against a
    different nullable declaration — which is why bounding one of them would have been half a
    fix, and why the two share one constant rather than two literals."""
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = _post(
            client,
            {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"thinkingConfig": {"thinkingBudget": requested}},
            },
        )

    assert response.status_code == 400, response.text
    assert "this gateway can account for" in response.text


class _Spy(BudgetLedger):
    """The real ledger, with a note of what was reserved through it.

    A **spy**, not a stand-in: every call is delegated, so the Lua script and its 64-bit arithmetic
    are the ones under test. What it adds is the one thing the surface cannot show — the figure
    `estimate` handed over — because `settle` corrects the reservation down to the real usage the
    moment the answer arrives, so by the time a second request could observe it, it is gone.
    """

    def __init__(self, runner: Any) -> None:
        super().__init__(runner)
        self.reserved: list[Amounts] = []

    async def reserve(self, scope_key: str, period_key: str, **kwargs: Any) -> str | None:
        self.reserved.append(kwargs["amounts"])
        return await super().reserve(scope_key, period_key, **kwargs)


async def test_a_total_nobody_wrote_is_clamped_rather_than_handed_over() -> None:
    """The third door, and the only one with nobody to refuse.

    The two above are fields a caller wrote. This one is a **sum**: the caller's cap, plus what the
    *model's own declaration* says an attachment of that type costs, plus a thinking budget. An
    operator writes the attachment estimate, and the gateway does not get to assume Management
    bounded it — the read-model is also reachable from Kafka, a seed and a direct database write,
    which is the argument `aira_common.patterns` makes for asking a question at both ends.

    There is no field to name in a refusal, so the estimate is clamped instead — safe exactly here
    and nowhere else: an estimate is deliberately approximate and `settle` replaces it with the real
    figure the moment the answer arrives. What must not happen is the counter being handed a number
    it cannot hold.
    """
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc"))
            session.add(
                ModelRead(
                    model="mock-1",
                    numeric_id=9001,
                    capabilities=["generate", "attachments"],
                    approved=True,
                    # Big enough that the **sum** overflows the counter, which a caller-sized
                    # number no longer can.
                    attachments={"media_types": {"application/pdf": {"tokens": 10**30}}},
                )
            )
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    period="day",
                    limit_requests=1000,
                    enabled=True,
                )
            )
            await session.commit()
        spy = _Spy(_runner())
        app.state.budgets._ledger = spy

        response = _post(
            client,
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "hi"},
                            {"inlineData": {"mimeType": "application/pdf", "data": "JVBERi0xLjQK"}},
                        ],
                    }
                ],
                # At the ceiling, which is allowed — the attachment estimate is what takes the
                # *sum* past it, and no single field a caller wrote is out of bounds.
                "generationConfig": {"maxOutputTokens": MAX_ACCOUNTABLE_TOKENS},
            },
        )

    assert response.status_code == 200, response.text
    assert not app.state.budgets._degradation.features, (
        "a total the counter cannot hold read as the counter store being away"
    )
    assert [amounts.tokens for amounts in spy.reserved] == [MAX_ACCOUNTABLE_TOKENS]


async def test_the_estimate_still_carries_the_figure_it_is_for() -> None:
    """The other half of the clamp, and the reason it is `min` rather than a constant.

    A clamp that flattened every estimate would pass the test above — and quietly stop the
    reservation bounding anything, which is `FRD-405` §4.2 switched off by the fix meant to protect
    it. The caller asked for 4 096 output tokens, so 4 096 is what has to be set aside.
    """
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        async with app.state.db_sessionmaker() as session:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    period="day",
                    limit_requests=1000,
                    enabled=True,
                )
            )
            await session.commit()
        spy = _Spy(_runner())
        app.state.budgets._ledger = spy

        response = _post(
            client,
            {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 4096},
            },
        )

    assert response.status_code == 200, response.text
    assert [amounts.tokens for amounts in spy.reserved] == [4096]


KIRA = "/kira/api/external/chat"


@pytest.mark.parametrize("requested", [2**63, 10**30])
async def test_the_kira_surface_refuses_it_in_its_own_words(requested: int) -> None:
    """The parity half, and it is about the **code** rather than about the bound.

    `check_declaration` is shared, so this request would be refused either way — as a
    `GeminiHTTPError`, which this surface renders as `400 VALIDATION_ERROR`. The model-cap version
    of the same mistake answers `422 MAX_TOKENS_EXCEEDS_CAP`, and *the code is this surface's
    contract*: a migrating client switches on it. One mistake with two codes depending on whether
    somebody catalogued a cap is the wrinkle the ceiling would otherwise have introduced.
    """
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = client.post(
            KIRA,
            json={"request": {"parts": [{"text": "hi"}]}, "model_id": 9001, "maxTokens": requested},
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "MAX_TOKENS_EXCEEDS_CAP", response.text
    assert "this gateway can account for" in response.text


async def test_an_ordinary_request_is_untouched() -> None:
    """The comparison, at the surface. A ceiling that refused real traffic would pass both tests
    above and take the product with it."""
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = _post(
            client,
            {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 4096},
            },
        )

    assert response.status_code == 200, response.text
