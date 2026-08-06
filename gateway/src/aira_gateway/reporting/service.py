"""Spend and usage aggregates over the request log (FRD-601).

Every dispatched request has been recorded since FRD-103 and priced since FRD-403; this is what
finally reads it. The figures are aggregated **in the database**: pulling a period's rows into the
process to sum them would move the whole window across the wire to compute four numbers from it,
and would grow with traffic the installation does not control.

Two rules carry over from the budget work and matter as much here:

- **Unpriced is not zero.** A request on a model with no price counts toward ``unpriced_requests``
  and toward nothing else. A total that quietly absorbs it reads as complete when it is not.
- **Money is an integer.** Nano-units throughout, rendered as an exact decimal string at the edge.

The visibility rule is resolved by the caller (see ``aira_gateway.api.reporting``) and arrives
here as a scope: ``None`` means every use case, a tuple means exactly those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import format_display
from aira_gateway.db.models import RequestLog

# What a caller may see. ``None`` is "every use case" — governance — and is deliberately distinct
# from the empty tuple, which is "no use case at all" and yields an empty report rather than
# everything. Confusing the two is the one mistake here that would leak every figure in the
# installation, so the type says which is which rather than relying on falsiness.
Scope = tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class Figures:
    """One row of the report: a group, and what happened in it."""

    key: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_nanos: int
    unpriced_requests: int
    failed_requests: int
    avg_latency_ms: int | None
    max_latency_ms: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            # Both forms, the same pair FRD-403 established: the string is what a human reads,
            # the integer is what a bar divides without a float touching a monetary figure.
            "cost_nanos": self.cost_nanos,
            "cost": format_display(self.cost_nanos),
            "unpriced_requests": self.unpriced_requests,
            "failed_requests": self.failed_requests,
            "avg_latency_ms": self.avg_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


_EMPTY = Figures("", 0, 0, 0, 0, 0, 0, 0, None, None)


def _measures() -> list[Any]:
    """The columns every breakdown reports, in one place so the rows cannot diverge."""
    return [
        func.count().label("requests"),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(RequestLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(RequestLog.cost_nanos), 0).label("cost_nanos"),
        # A request whose model had no price. Counted, never summed as zero.
        func.sum(case((RequestLog.cost_nanos.is_(None), 1), else_=0)).label("unpriced_requests"),
        # A failed request still consumed a rate limit and possibly an upstream call. It is part
        # of what happened, so it is reported rather than filtered out (FR-6).
        func.sum(case((RequestLog.status >= 400, 1), else_=0)).label("failed_requests"),
        func.avg(RequestLog.latency_ms).label("avg_latency_ms"),
        func.max(RequestLog.latency_ms).label("max_latency_ms"),
    ]


def _figures(key: str, row: Any) -> Figures:
    return Figures(
        key=key,
        requests=int(row.requests or 0),
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
        total_tokens=int(row.total_tokens or 0),
        cost_nanos=int(row.cost_nanos or 0),
        unpriced_requests=int(row.unpriced_requests or 0),
        failed_requests=int(row.failed_requests or 0),
        avg_latency_ms=None if row.avg_latency_ms is None else round(float(row.avg_latency_ms)),
        max_latency_ms=None if row.max_latency_ms is None else int(row.max_latency_ms),
    )


class ReportingService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def report(self, scope: Scope, start: datetime, end: datetime) -> dict[str, Any]:
        """Totals and breakdowns for ``scope`` over ``[start, end)``.

        The window is half-open on purpose: a report for August and one for September must not
        both contain the request that arrived at midnight.
        """
        async with self._sessionmaker() as session:
            totals = await self._totals(session, scope, start, end)
            return {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "totals": totals.as_dict(),
                "by_use_case": [
                    row.as_dict()
                    for row in await self._grouped(session, scope, start, end, RequestLog.use_case)
                ],
                "by_model": [
                    row.as_dict()
                    for row in await self._grouped(session, scope, start, end, RequestLog.model)
                ],
                "by_member": [
                    row.as_dict()
                    for row in await self._grouped(session, scope, start, end, RequestLog.subject)
                ],
                # Why requests ended the way they did (FRD-122 FR-8). Without it a use case
                # grinding against its budget wall all day is indistinguishable from a healthy
                # one — the refusals were 429s and nothing said which control produced them.
                "by_outcome": [
                    row.as_dict()
                    for row in await self._grouped(session, scope, start, end, RequestLog.outcome)
                ],
            }

    def _window(self, statement: Any, scope: Scope, start: datetime, end: datetime) -> Any:
        statement = statement.where(RequestLog.created_at >= start, RequestLog.created_at < end)
        if scope is None:
            return statement  # governance: every use case
        if not scope:
            # No memberships and no oversight. `where(false)` rather than skipping the query, so
            # the caller gets a shaped empty report instead of a special case to handle.
            return statement.where(RequestLog.use_case.is_(None) & RequestLog.use_case.isnot(None))
        return statement.where(RequestLog.use_case.in_(scope))

    async def _totals(
        self, session: AsyncSession, scope: Scope, start: datetime, end: datetime
    ) -> Figures:
        row = (await session.execute(self._window(select(*_measures()), scope, start, end))).one()
        return _figures("total", row)

    async def _grouped(
        self,
        session: AsyncSession,
        scope: Scope,
        start: datetime,
        end: datetime,
        column: Any,
    ) -> list[Figures]:
        statement = self._window(select(column.label("key"), *_measures()), scope, start, end)
        result = await session.execute(
            statement.group_by(column).order_by(
                func.coalesce(func.sum(RequestLog.cost_nanos), 0).desc()
            )
        )
        # A NULL group is a real group, not an absence: rows written before a column existed, and
        # requests with no use case, both land here. Labelling them keeps them countable instead of
        # quietly dropping them out of a total.
        return [_figures(row.key or "(none)", row) for row in result]
