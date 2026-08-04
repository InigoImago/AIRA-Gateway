"""Budget enforcement + usage accounting (FRD-401).

``guard`` is called pre-dispatch: it loads the budgets applicable to the request's use case +
subject, checks the current period's usage, and raises ``BudgetExceeded`` if a limit is already
met. ``record`` is called post-dispatch to increment the counters. Usage is keyed by
``(scope_key, period_key)`` so it resets naturally at each day/month boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.db.models import BudgetRead, BudgetUsage


def _period_key(period: str, now: datetime) -> str:
    return now.strftime("%Y-%m-%d") if period == "day" else now.strftime("%Y-%m")


def _scope_key(budget: BudgetRead) -> str:
    if budget.scope == "use_case":
        return f"uc:{budget.use_case}"
    return f"member:{budget.use_case}:{budget.subject}"


class BudgetService:
    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], *, enforce: bool = True
    ) -> None:
        self._sessionmaker = sessionmaker
        self._enforce = enforce

    async def guard(
        self, use_case: str | None, subject: str | None, now: datetime | None = None
    ) -> list[BudgetRead]:
        """Check applicable budgets; raise ``BudgetExceeded`` if over. Returns them for record."""
        if not self._enforce or not use_case:
            return []
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = await self._applicable(session, use_case, subject)
            for budget in budgets:
                tokens, requests = await self._usage(
                    session, _scope_key(budget), _period_key(budget.period, now)
                )
                if budget.limit_requests is not None and requests >= budget.limit_requests:
                    raise BudgetExceeded(
                        f"Request budget exhausted for {budget.scope} ({budget.period})."
                    )
                if budget.limit_tokens is not None and tokens >= budget.limit_tokens:
                    raise BudgetExceeded(
                        f"Token budget exhausted for {budget.scope} ({budget.period})."
                    )
        return budgets

    async def record(
        self, budgets: list[BudgetRead], tokens: int, now: datetime | None = None
    ) -> None:
        if not budgets:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            for budget in budgets:
                scope_key = _scope_key(budget)
                period_key = _period_key(budget.period, now)
                record = await session.get(BudgetUsage, (scope_key, period_key))
                if record is None:
                    record = BudgetUsage(
                        scope_key=scope_key, period_key=period_key, tokens=0, requests=0
                    )
                    session.add(record)
                record.tokens += tokens
                record.requests += 1
            await session.commit()

    async def usage(self, use_case: str, now: datetime | None = None) -> list[dict[str, int]]:
        """Current-period usage per budget for a use case (for the UI consumption view, FRD-402)."""
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(BudgetRead).where(BudgetRead.use_case == use_case)
            )
            out: list[dict[str, int]] = []
            for budget in result.scalars():
                tokens, requests = await self._usage(
                    session, _scope_key(budget), _period_key(budget.period, now)
                )
                out.append({"id": budget.id, "used_tokens": tokens, "used_requests": requests})
            return out

    async def _applicable(
        self, session: AsyncSession, use_case: str, subject: str | None
    ) -> list[BudgetRead]:
        result = await session.execute(
            select(BudgetRead).where(BudgetRead.use_case == use_case, BudgetRead.enabled.is_(True))
        )
        budgets: list[BudgetRead] = []
        for budget in result.scalars():
            if (
                budget.scope == "use_case"
                or budget.scope == "member"
                and subject
                and budget.subject == subject
            ):
                budgets.append(budget)
        return budgets

    async def _usage(
        self, session: AsyncSession, scope_key: str, period_key: str
    ) -> tuple[int, int]:
        record = await session.get(BudgetUsage, (scope_key, period_key))
        return (record.tokens, record.requests) if record else (0, 0)
