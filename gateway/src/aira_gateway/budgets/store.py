"""The Postgres side of budgets: which budgets bind a request, and the `budget_usage` counters.

Postgres is the system of record; the Redis ledger is a cache seeded from it (`FRD-405`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.db.models import BudgetRead, BudgetUsage
from aira_gateway.scopes import Scope


@dataclass(frozen=True, slots=True)
class Usage:
    """What has been consumed in one (scope, period) so far."""

    tokens: int
    requests: int
    cost_nanos: int
    unpriced_requests: int


async def applicable(
    session: AsyncSession, use_case: str | None, subject: str | None
) -> list[BudgetRead]:
    """Every enabled budget that binds this request.

    A request naming a use case reads that use case's rows; one naming none reads the
    installation's (`FRD-610`). `Scope.applying` decides which of the fetched rows bind — the one
    place a scope is defined, which the rate limiter follows too.
    """
    result = await session.execute(
        select(BudgetRead).where(
            BudgetRead.use_case == (use_case or ""), BudgetRead.enabled.is_(True)
        )
    )
    return [
        budget
        for budget in result.scalars()
        if Scope.applying(scope=budget.scope, use_case=budget.use_case, caller=subject) is not None
    ]


async def read_usage(session: AsyncSession, scope_key: str, period_key: str) -> Usage:
    record = await session.get(BudgetUsage, (scope_key, period_key))
    if record is None:
        return Usage(0, 0, 0, 0)
    return Usage(record.tokens, record.requests, record.cost_nanos, record.unpriced_requests)


def accumulate(
    session: AsyncSession,
    *,
    scope_key: str,
    period_key: str,
    tokens: int,
    requests: int,
    cost_nanos: int | None,
) -> Any:
    """One statement that inserts the counter or adds to it.

    The arithmetic happens in the database, where the row is locked for the statement: a
    read-then-write loses concurrent increments (the record drifts **below** the truth, in the
    direction that spends money), and two first requests of a period both inserting raise a unique
    violation after the request was served. ``ON CONFLICT`` is spelled the same by Postgres and
    SQLite and by nobody else, hence the dispatch on the dialect.

    ``cost_nanos`` of ``None`` means the model had no price on file; it is counted under
    ``unpriced_requests`` rather than as zero (`FRD-403`).
    """
    unpriced = requests if cost_nanos is None else 0
    values = {
        "scope_key": scope_key,
        "period_key": period_key,
        "tokens": tokens,
        "requests": requests,
        "cost_nanos": cost_nanos or 0,
        "unpriced_requests": unpriced,
    }
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(BudgetUsage).values(**values)

    columns = BudgetUsage.__table__.c
    return statement.on_conflict_do_update(
        index_elements=["scope_key", "period_key"],
        set_={
            "tokens": columns.tokens + tokens,
            "requests": columns.requests + requests,
            "cost_nanos": columns.cost_nanos + (cost_nanos or 0),
            "unpriced_requests": columns.unpriced_requests + unpriced,
        },
    )
