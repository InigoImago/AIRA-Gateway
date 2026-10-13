"""`make loadtest-clean` removes the load run's rows and nobody else's.

The cleanup matched `budget_usage.scope_key LIKE '%lt-%'` — `lt-` *anywhere* in the key — so it
deleted the budget **system of record** of `uc:default-assistant`, `uc:consult-team` and
`member:hr:walt-smith@corp`: their consumption was forgiven, silently, by a command whose name says
it tidies up after a measurement. Every other table was matched by the `lt-` prefix, which takes a
real use case that happens to be called `lt-…` with it — audit trail, keys, budgets and members.

Run against the gateway's own schema on SQLite, with rows a load run makes and rows it does not.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tools.loadtest import seed

from aira_gateway.db.base import build_engine, create_all
from aira_gateway.db.models import BudgetUsage, RequestLog, UseCaseRead

#: Use cases the seed does not make, each a way of resembling one it does.
STRANGERS = ("lt-payroll", "default-assistant", "consult-team", "hr")
#: Counter keys of those use cases and people: `lt-` inside, `lt-` as a prefix, a look-alike slug.
FOREIGN_COUNTERS = (
    "uc:default-assistant",
    "uc:consult-team",
    "member:hr:walt-smith@corp",
    "uc:lt-payroll",
    "member:lt-payroll:a.person",
    "uc:lt-chat-eu",
)


async def _seeded(connection: Any) -> None:
    """One request, counter and use case for every seeded slug and for every stranger — written
    through the gateway's models, so every column gets the value the gateway would give it."""
    seeded = sorted(seed.BY_SLUG)
    counters = [
        *(f"uc:{slug}" for slug in seeded),
        *(f"member:{slug}:{seed.subject_for(slug, 0)}" for slug in seeded),
        *FOREIGN_COUNTERS,
    ]
    async with AsyncSession(bind=connection) as session:
        for slug in (*seeded, *STRANGERS):
            session.add(UseCaseRead(slug=slug, name=slug))
            session.add(
                RequestLog(
                    subject="s",
                    auth_method="api_key",
                    api="gemini",
                    operation="generate",
                    model="m",
                    status=200,
                    use_case=slug,
                )
            )
        for key in counters:
            session.add(BudgetUsage(scope_key=key, period_key="2026-10"))
        await session.flush()


async def _left(connection: Any, query: str) -> set[str]:
    return {row[0] for row in (await connection.execute(text(query))).all()}


@pytest.mark.asyncio
async def test_the_cleanup_takes_the_load_runs_rows_and_leaves_everyone_elses() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    try:
        async with engine.begin() as connection:
            await _seeded(connection)
            await seed.clean(connection)

            counters = await _left(connection, "SELECT scope_key FROM budget_usage")
            requests = await _left(connection, "SELECT use_case FROM request_logs")
            use_cases = await _left(connection, "SELECT slug FROM use_cases")
    finally:
        await engine.dispose()

    assert counters == set(FOREIGN_COUNTERS), "somebody else's consumption was forgiven"
    assert requests == set(STRANGERS), "somebody else's audit trail was deleted"
    assert use_cases == set(STRANGERS)
