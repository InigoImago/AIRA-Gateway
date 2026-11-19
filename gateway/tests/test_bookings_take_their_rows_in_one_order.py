"""Every booking upserts its `budget_usage` rows in one order: the counters' own key.

A booking writes one row per budget that binds the request, in one transaction, and each upsert
keeps its row locked until the commit. Two bookings of the same use case that take two rows in
opposite orders deadlock on Postgres, which aborts one of them: that request's spend never reaches
the system of record while its audit row carries the cost, and every other booking of the use case
waits behind the locks until the deadlock is detected. The orders differed in practice — a
settlement handed its enforced budgets first and its lifted ones after, a pipeline step's booking
the rows in whatever order the database returned them.

SQLite takes no row locks, so what is held here is the property the code controls: the order the
upserts are handed to the database, whatever order the caller passed the budgets in.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class _Upserts:
    """The `budget_usage` rows handed to the database, in the order they were handed over."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def seen(self, _conn: Any, _cursor: Any, statement: str, _params: Any, context: Any, *_: Any):  # noqa: ANN201
        if statement.lstrip().upper().startswith("INSERT INTO BUDGET_USAGE"):
            values = context.compiled_parameters[0]
            self.rows.append((values["scope_key"], values["period_key"]))

    def taken(self) -> list[tuple[str, str]]:
        rows, self.rows = self.rows, []
        return rows


@pytest_asyncio.fixture
async def booked() -> AsyncIterator[tuple[async_sessionmaker, _Upserts]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    upserts = _Upserts()
    event.listen(engine.sync_engine, "before_cursor_execute", upserts.seen)
    yield build_sessionmaker(engine), upserts
    await engine.dispose()


async def _budgets(sessionmaker: async_sessionmaker, *rows: dict[str, Any]) -> list[BudgetRead]:
    async with sessionmaker() as session:
        for row in rows:
            session.add(BudgetRead(use_case="uc", subject="", **row))
        await session.commit()
    async with sessionmaker() as session:
        return [await session.get(BudgetRead, row["id"]) for row in rows]


async def test_a_settlement_and_a_pipeline_booking_take_the_rows_in_one_order(booked) -> None:
    """The two bookings that met in practice: a lifted monthly budget beside an enforced daily
    one, the scan returning the lifted one first."""
    sessionmaker, upserts = booked
    await _budgets(
        sessionmaker,
        {
            "id": 1,
            "scope": "use_case",
            "period": "month",
            "enabled": False,
            "limit_cost_nanos": 10**12,
        },
        {
            "id": 2,
            "scope": "use_case",
            "period": "day",
            "enabled": True,
            "limit_cost_nanos": 10**11,
        },
    )
    service = BudgetService(sessionmaker)

    reservation = await service.guard("uc", "alice", NOW, estimated=Amounts(requests=1))
    await service.settle(reservation, 100, cost_nanos=5, now=NOW)
    settled = upserts.taken()
    await service.book_side_call("uc", "bob", 20, cost_nanos=1, now=NOW)
    side_call = upserts.taken()

    assert settled == side_call == sorted(settled), (
        f"a settlement locks {settled}, a pipeline booking {side_call}: two that overlap deadlock"
    )


async def test_whatever_order_the_budgets_come_in_the_rows_are_upserted_in_one(booked) -> None:
    sessionmaker, upserts = booked
    budgets = await _budgets(
        sessionmaker,
        {"id": 1, "scope": "use_case", "period": "month", "enabled": True},
        {"id": 2, "scope": "use_case", "period": "day", "enabled": True},
        {"id": 3, "scope": "each_member", "period": "month", "enabled": False},
    )
    service = BudgetService(sessionmaker)

    orders = set()
    for order in itertools.permutations(budgets):
        await service.record(list(order), 1, now=NOW, subject="alice")
        orders.add(tuple(upserts.taken()))

    assert len(orders) == 1, f"the rows were upserted in {len(orders)} different orders: {orders}"
    (only,) = orders
    assert list(only) == sorted(only) and len(only) == 3
