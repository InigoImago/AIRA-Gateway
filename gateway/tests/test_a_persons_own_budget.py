"""A person's own budget, in the hierarchy the owner set (`FRD-407`, `ADR-0044`).

The use case's budget bounds everybody together; a person's own budget takes the place of the one
for everybody **in its period**; everybody else keeps the one for everybody. Each property is
asserted on what decides — `scopes.binding` — and on what a caller meets: a refusal, a figure, the
allowance their own client reads.

Both ways a reservation can be held are run, because the hierarchy is applied before either is
reached and a defect in one of them would be invisible to a test of the other: the shared counter
store (the real Lua scripts on `fakeredis`) and the Postgres path a gateway falls back to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import BudgetRead, BudgetUsage, ModelRead, RateLimitRead, UseCaseRead
from aira_gateway.scopes import EACH_MEMBER, MEMBER, USE_CASE, Scope, binding

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


# == the rule ======================================================================================


@dataclass(frozen=True)
class _Row:
    id: int
    scope: str
    period: str = "month"
    subject: str = ""
    enabled: bool = True
    use_case: str = "uc"


def _ids(rows: list[_Row], caller: str | None) -> list[int]:
    return [row.id for row in binding(rows, caller=caller, slot=lambda row: row.period)]


def test_a_persons_own_row_binds_them_and_nobody_else() -> None:
    """Named by the key their allowance is counted under, so it binds that person and no other —
    and the counter it reads is the one their per-person budget keeps."""
    own = Scope.applying(scope=MEMBER, use_case="uc", caller="alice", named="alice")
    assert own == Scope("uc", "alice")
    assert own.usage_key == "member:uc:alice"
    assert Scope.applying(scope=MEMBER, use_case="uc", caller="bob", named="alice") is None
    # A row naming nobody binds nobody, and a request with no person is bound by no person's row.
    assert Scope.applying(scope=MEMBER, use_case="uc", caller="alice", named="") is None
    assert Scope.applying(scope=MEMBER, use_case="uc", caller=None, named="") is None
    # The same counter as the row for everybody, by construction: replacing one by the other
    # mid-period hands out no fresh allowance.
    everybody = Scope.applying(scope=EACH_MEMBER, use_case="uc", caller="alice")
    assert everybody is not None and everybody.usage_key == own.usage_key


def test_a_persons_own_budget_takes_the_place_of_the_one_for_everybody() -> None:
    rows = [_Row(1, USE_CASE), _Row(2, EACH_MEMBER), _Row(3, MEMBER, subject="alice")]
    assert _ids(rows, "alice") == [1, 3]
    # Everybody else keeps the one for everybody, and the shared pot binds both.
    assert _ids(rows, "bob") == [1, 2]


def test_it_takes_the_place_only_in_its_own_period() -> None:
    """A monthly exception leaves the daily per-person limit where it is."""
    rows = [
        _Row(1, EACH_MEMBER, period="day"),
        _Row(2, EACH_MEMBER, period="month"),
        _Row(3, MEMBER, period="month", subject="alice"),
    ]
    assert _ids(rows, "alice") == [1, 3]


def test_switched_off_it_hands_the_person_back_to_the_one_for_everybody() -> None:
    """Never to no limit at all: lifting somebody's own budget must not lift every limit they
    had, which is the direction a budget must not be wrong in."""
    rows = [_Row(2, EACH_MEMBER), _Row(3, MEMBER, subject="alice", enabled=False)]
    assert _ids(rows, "alice") == [2]


def test_switched_off_with_nothing_beneath_it_it_is_still_counted() -> None:
    """A lifted budget refuses nothing and still counts (`FRD-401`): with no row for everybody in
    that period, the person's own is the one that is booked."""
    rows = [_Row(3, MEMBER, subject="alice", enabled=False)]
    assert _ids(rows, "alice") == [3]


def test_on_it_binds_whatever_the_row_for_everybody_says() -> None:
    rows = [_Row(2, EACH_MEMBER, enabled=False), _Row(3, MEMBER, subject="alice")]
    assert _ids(rows, "alice") == [3]


def test_two_rows_off_still_book_one_counter_once() -> None:
    """They share one counter: two rows bound would book every request twice."""
    rows = [_Row(2, EACH_MEMBER, enabled=False), _Row(3, MEMBER, subject="alice", enabled=False)]
    assert _ids(rows, "alice") == [3]


def test_the_order_of_the_rows_decides_nothing() -> None:
    rows = [_Row(3, MEMBER, subject="alice"), _Row(2, EACH_MEMBER), _Row(1, USE_CASE)]
    assert sorted(_ids(rows, "alice")) == [1, 3]
    assert sorted(_ids(list(reversed(rows)), "alice")) == [1, 3]


# == enforcement, on both stores ===================================================================


class _Runner:
    """The ledger's scripts on an in-process Redis that runs Lua."""

    def __init__(self) -> None:
        self._client = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        return await self._client.register_script(script)(keys=list(keys), args=list(args))

    async def close(self) -> None:
        await self._client.aclose()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(params=["shared counter", "postgres"])
async def service(request: pytest.FixtureRequest, engine: AsyncEngine) -> AsyncIterator[Any]:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    if request.param == "postgres":
        yield BudgetService(sessionmaker)
        return
    runner = _Runner()
    yield BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    await runner.close()


async def _add(service: BudgetService, **fields: Any) -> None:
    row = {"use_case": "uc", "subject": "", "period": "month", "enabled": True, **fields}
    async with service._sessionmaker() as session:
        session.add(BudgetRead(**row))
        await session.commit()


async def _spend(service: BudgetService, who: str) -> None:
    """One request by ``who``: reserved, then settled at one request and ten tokens."""
    reservation = await service.guard("uc", who, NOW)
    await service.settle(reservation, 10, now=NOW)


async def _counters(service: BudgetService) -> dict[str, int]:
    async with service._sessionmaker() as session:
        rows = (await session.execute(select(BudgetUsage))).scalars()
        return {row.scope_key: row.requests for row in rows}


async def test_a_higher_own_budget_lets_one_person_past_the_default(service: Any) -> None:
    """The point of the hierarchy: an exception upwards, which the removed scope could not say."""
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=1)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=3)

    for _ in range(3):
        await _spend(service, "alice")
    with pytest.raises(BudgetExceeded):
        await _spend(service, "alice")

    await _spend(service, "bob")
    with pytest.raises(BudgetExceeded):
        await _spend(service, "bob")


async def test_a_lower_own_budget_refuses_first_and_says_whose_it_is(service: Any) -> None:
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=10)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=1)

    await _spend(service, "alice")
    with pytest.raises(BudgetExceeded) as refused:
        await _spend(service, "alice")
    # Named as hers: a person refused by a budget set for them alone asks somebody else than one
    # refused by the allowance everybody has.
    assert "individual member" in refused.value.message, refused.value.message


async def test_the_use_cases_pot_still_bounds_a_person_with_their_own(service: Any) -> None:
    await _add(service, id=1, scope=USE_CASE, limit_requests=2)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=50)

    await _spend(service, "alice")
    await _spend(service, "alice")
    with pytest.raises(BudgetExceeded) as refused:
        await _spend(service, "alice")
    assert "use case" in refused.value.message


async def test_one_request_is_booked_once_whichever_row_binds(service: Any) -> None:
    """Both rows bind the same counter; booking both would count every request twice."""
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=10)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=10)

    await _spend(service, "alice")

    assert await _counters(service) == {"member:uc:alice": 1}


async def test_an_own_budget_set_mid_period_starts_from_what_was_spent(service: Any) -> None:
    """No fresh allowance by replacing one row with the other: the counter is the person's."""
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=10)
    for _ in range(2):
        await _spend(service, "alice")

    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=2)

    with pytest.raises(BudgetExceeded):
        await _spend(service, "alice")


async def test_a_monthly_own_budget_leaves_the_daily_one_for_everybody(service: Any) -> None:
    """Enforcement keys the hierarchy on the period: a larger monthly budget of her own does not
    lift the daily limit everybody has."""
    await _add(service, id=1, scope=EACH_MEMBER, period="day", limit_requests=1)
    await _add(service, id=2, scope=MEMBER, subject="alice", period="month", limit_requests=50)

    await _spend(service, "alice")
    with pytest.raises(BudgetExceeded) as refused:
        await _spend(service, "alice")
    assert "(day)" in refused.value.message, refused.value.message


async def test_switched_off_the_person_is_held_to_the_one_for_everybody(service: Any) -> None:
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=1)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=5, enabled=False)

    await _spend(service, "alice")
    with pytest.raises(BudgetExceeded) as refused:
        await _spend(service, "alice")
    assert "individual" not in refused.value.message


# == what a reader is told =========================================================================


async def test_the_reader_is_told_which_row_binds_them(service: Any) -> None:
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=10)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=3)
    await _spend(service, "alice")

    alice = {row["id"]: row for row in await service.usage("uc", NOW, subject="alice")}
    bob = {row["id"]: row for row in await service.usage("uc", NOW, subject="bob")}

    # Her own: her figure, and binding her.
    assert alice[2]["binds"] is True
    assert alice[2]["measured_for"] == "alice"
    assert alice[2]["used_requests"] == 1 and alice[2]["remaining_requests"] == 2
    # The one for everybody gives way for her, says to which, and carries no figure against a limit
    # that is not hers.
    assert alice[1]["binds"] is False and alice[1]["replaced_by"] == 2
    assert alice[1]["used_requests"] is None

    # To somebody else, her budget binds nothing, names nobody and measures nothing.
    assert bob[2]["binds"] is False and bob[2]["replaced_by"] is None
    assert bob[2]["measured_for"] is None and bob[2]["used_requests"] is None
    assert bob[1]["binds"] is True and bob[1]["measured_for"] == "bob"


async def test_a_switched_off_own_budget_names_the_one_that_binds_instead(service: Any) -> None:
    await _add(service, id=1, scope=EACH_MEMBER, limit_requests=10)
    await _add(service, id=2, scope=MEMBER, subject="alice", limit_requests=3, enabled=False)

    rows = {row["id"]: row for row in await service.usage("uc", NOW, subject="alice")}

    assert rows[1]["binds"] is True
    assert rows[2]["binds"] is False and rows[2]["replaced_by"] == 1


# == what a caller's own client reads ==============================================================


class _SignedIn:
    """Stand-in OIDC validator: every token is ``username``, a member of ``uc``."""

    def __init__(self, username: str) -> None:
        self.username = username

    def validate(self, token: str) -> Principal:
        return Principal(
            subject=f"{self.username}-sub", method="oidc", username=self.username, use_cases=("uc",)
        )


async def test_my_budget_lists_only_what_binds_me() -> None:
    """`/v1beta/me/budget` is what OpenCode's sidebar draws: a budget that gives way for me, or
    one set for a colleague, would put a limit beside my figure that is not mine."""
    app = create_app(GatewaySettings(auth_required=True, demo_mode=True, log_queue_size=0))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=["mock-1"]))
            session.add(ModelRead(model="mock-1", numeric_id=1004, capabilities=["generate"]))
            session.add_all(
                [
                    BudgetRead(
                        id=1, use_case="uc", scope=USE_CASE, period="month", limit_cost_nanos=10**12
                    ),
                    BudgetRead(
                        id=2, use_case="uc", scope=EACH_MEMBER, period="month", limit_requests=5
                    ),
                    BudgetRead(
                        id=3,
                        use_case="uc",
                        scope=MEMBER,
                        subject="alice",
                        period="month",
                        limit_requests=50,
                    ),
                ]
            )
            await session.commit()

        answers = {}
        for who in ("alice", "bob"):
            app.state.oidc_validator = _SignedIn(who)
            answer = client.get(
                "/v1beta/me/budget",
                headers={"authorization": "Bearer t", "x-aira-use-case": "uc"},
            )
            assert answer.status_code == 200, answer.text
            answers[who] = sorted(row["id"] for row in answer.json()["allowances"])

    assert answers == {"alice": [1, 3], "bob": [1, 2]}


# == the rate limiter follows the same rule ========================================================


async def test_the_rate_limiter_takes_one_per_person_bucket_whichever_row_binds() -> None:
    """Nothing offers a person their own rate limit, and the limiter follows the rule anyway: two
    per-person rows take one bucket — the person's — once, never twice."""
    from aira_gateway.ratelimit.buckets import InMemoryTokenBucket
    from aira_gateway.ratelimit.service import RateLimitService

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        session.add_all(
            [
                RateLimitRead(id=1, use_case="uc", scope=EACH_MEMBER, limit_rpm=60, enabled=True),
                RateLimitRead(
                    id=2, use_case="uc", scope=MEMBER, subject="alice", limit_rpm=1, enabled=True
                ),
            ]
        )
        await session.commit()
    limiter = RateLimitService(sessionmaker, InMemoryTokenBucket())
    records = await limiter._config("uc")

    alice = limiter._applicable(records, "uc", "alice")
    bob = limiter._applicable(records, "uc", "bob")
    await engine.dispose()

    assert [(bucket.key, bucket.capacity) for bucket in alice] == [("rl:{uc}:member:alice", 1)]
    assert [(bucket.key, bucket.capacity) for bucket in bob] == [("rl:{uc}:member:bob", 60)]
