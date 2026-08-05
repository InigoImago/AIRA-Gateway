"""Budget enforcement + usage accounting (FRD-401).

``guard`` is called pre-dispatch: it loads the budgets applicable to the request's use case +
subject, checks the current period's usage, and raises ``BudgetExceeded`` if a limit is already
met. ``record`` is called post-dispatch to increment the counters. Usage is keyed by
``(scope_key, period_key)`` so it resets naturally at each day/month boundary.

A budget may cap **cost**, tokens, or request count (FRD-403). Cost is the limit that answers
what a budget is normally asked, because a token differs in price by more than an order of
magnitude between models; the count limits remain available as a volume guard. Money is carried
as integer nano-units throughout — see ``aira_common.money`` for why never as a float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import format_display
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.db.models import BudgetRead, BudgetUsage


@dataclass(frozen=True, slots=True)
class _Usage:
    """What has been consumed in one (scope, period) so far."""

    tokens: int
    requests: int
    cost_nanos: int
    unpriced_requests: int


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
                usage = await self._usage(
                    session, _scope_key(budget), _period_key(budget.period, now)
                )
                if (
                    budget.limit_cost_nanos is not None
                    and usage.cost_nanos >= budget.limit_cost_nanos
                ):
                    raise BudgetExceeded(
                        f"Cost budget exhausted for {budget.scope} ({budget.period})."
                    )
                if budget.limit_requests is not None and usage.requests >= budget.limit_requests:
                    raise BudgetExceeded(
                        f"Request budget exhausted for {budget.scope} ({budget.period})."
                    )
                if budget.limit_tokens is not None and usage.tokens >= budget.limit_tokens:
                    raise BudgetExceeded(
                        f"Token budget exhausted for {budget.scope} ({budget.period})."
                    )
        return budgets

    async def record(
        self,
        budgets: list[BudgetRead],
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Book one request against every applicable budget.

        Both extra arguments are keyword-only on purpose: an amount of money and a timestamp
        next to each other as positionals is exactly how a caller ends up booking the wrong
        figure without anything failing.

        ``cost_nanos`` is ``None`` when the model has no price on file. Such a request is
        counted under ``unpriced_requests`` rather than as costing zero: a spend figure that
        silently omits traffic is worse than one that admits what it does not know.
        """
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
                        scope_key=scope_key,
                        period_key=period_key,
                        tokens=0,
                        requests=0,
                        cost_nanos=0,
                        unpriced_requests=0,
                    )
                    session.add(record)
                record.tokens += tokens
                record.requests += 1
                if cost_nanos is None:
                    record.unpriced_requests += 1
                else:
                    record.cost_nanos += cost_nanos
            await session.commit()

    async def usage(self, use_case: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Current-period usage per budget for a use case (for the UI consumption view, FRD-402).

        Cost is reported both as an exact decimal string and as raw nano-units: the string is
        what a human reads, the integer is what a progress bar can divide without a float
        creeping into a monetary figure.
        """
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(BudgetRead).where(BudgetRead.use_case == use_case)
            )
            out: list[dict[str, Any]] = []
            for budget in result.scalars():
                usage = await self._usage(
                    session, _scope_key(budget), _period_key(budget.period, now)
                )
                out.append(
                    {
                        "id": budget.id,
                        "used_tokens": usage.tokens,
                        "used_requests": usage.requests,
                        "used_cost_nanos": usage.cost_nanos,
                        "used_cost": format_display(usage.cost_nanos),
                        "unpriced_requests": usage.unpriced_requests,
                    }
                )
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

    async def _usage(self, session: AsyncSession, scope_key: str, period_key: str) -> _Usage:
        record = await session.get(BudgetUsage, (scope_key, period_key))
        if record is None:
            return _Usage(0, 0, 0, 0)
        return _Usage(record.tokens, record.requests, record.cost_nanos, record.unpriced_requests)
