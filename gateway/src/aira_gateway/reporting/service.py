"""Spend and usage aggregates over the request log (`FRD-601`).

Aggregated **in the database**: pulling a window's rows into the process would move the whole period
across the wire to compute a few numbers, and grow with traffic nobody here controls. Two rules from
the budget work hold here too:

- **Unpriced is not zero, and zero is not unknown.** A request *served* on a model with no price
  counts toward ``unpriced_requests`` and toward nothing else.
- **Money is an integer**: nano-units throughout, rendered as an exact decimal string at the edge.

The caller resolves visibility (`api.reporting.visible_scope`) and passes it in as a `Scope`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import format_display
from aira_gateway.audit import Outcome
from aira_gateway.db.models import RequestLog

#: What a caller may see: ``None`` is every use case, a tuple exactly those, and the empty tuple
#: none — an empty report. Confusing the first with the last would leak every figure, so the type
#: says which is which rather than relying on falsiness.
Scope = tuple[str, ...] | None

#: How a **person** is grouped (`FRD-606`): the name where the credential carried one, the subject
#: otherwise. An OIDC subject is a directory id and an API key's is its owner's username, so
#: grouping by subject alone reports one person as two rows. A display only; `subject` stays the
#: identity.
_PERSON = func.coalesce(RequestLog.username, RequestLog.subject)


@dataclass(frozen=True, slots=True)
class Figures:
    """One row of the report: a group, and what happened in it."""

    key: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    #: Of the prompt tokens, how many came from a provider's cache (`FRD-133` FR-5): a cache that
    #: silently stopped working otherwise looks exactly like an expensive month.
    cached_input_tokens: int
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
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.total_tokens,
            # Both forms (`FRD-403`): the string for a human, the integer for a bar to divide
            # without a float touching a monetary figure.
            "cost_nanos": self.cost_nanos,
            "cost": format_display(self.cost_nanos),
            "unpriced_requests": self.unpriced_requests,
            "failed_requests": self.failed_requests,
            "avg_latency_ms": self.avg_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


def _measures() -> list[Any]:
    """The columns every breakdown reports, in one place so the rows cannot diverge.

    ``requests`` counts every model call, including those a pipeline step made on the caller's
    behalf (`pipeline:<step>` rows): they cost money, and the budgets count them the same way.
    """
    return [
        func.count().label("requests"),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(RequestLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(RequestLog.cost_nanos), 0).label("cost_nanos"),
        # Unpriced means *served* on a model with no price. A refused request has no cost either,
        # because nothing ran — counting it would make the "spend is a lower bound" caveat
        # permanent, and a warning that is always there is one nobody reads.
        func.sum(
            case(
                (
                    (RequestLog.cost_nanos.is_(None))
                    & (
                        (RequestLog.outcome == Outcome.SERVED)
                        # NULL: a row from before `FRD-122`, when only served requests were logged.
                        | (RequestLog.outcome.is_(None))
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("unpriced_requests"),
        # A failed request still used a rate limit and possibly an upstream call (FR-6).
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
        cached_input_tokens=int(row.cached_input_tokens or 0),
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

        Half-open, so reports for August and September do not both count the request that arrived
        at midnight.
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
                # Why requests ended as they did (`FRD-122` FR-8): which control refused them.
                "by_outcome": [
                    row.as_dict()
                    for row in await self._grouped(session, scope, start, end, RequestLog.outcome)
                ],
                # One person across credentials (`FRD-606`). `by_member` stays keyed on `subject`,
                # which is what every counter and budget is keyed on.
                "by_person": await self._by_person(session, scope, start, end),
            }

    async def _by_person(
        self, session: AsyncSession, scope: Scope, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Each person's figures, with their credentials' shares shown apart and together.

        Two queries rather than one folded in Python: the totals come from the database like every
        other figure, so they cannot disagree with their parts.
        """
        totals = {row.key: row for row in await self._grouped(session, scope, start, end, _PERSON)}
        statement = self._window(
            select(
                _PERSON.label("key"),
                RequestLog.auth_method.label("method"),
                *_measures(),
            ),
            scope,
            start,
            end,
        )
        split: dict[str, dict[str, Any]] = {}
        for row in await session.execute(statement.group_by(_PERSON, RequestLog.auth_method)):
            key = row.key or "(none)"
            split.setdefault(key, {})[row.method or "(none)"] = _figures(key, row).as_dict()

        return [
            {**figures.as_dict(), "by_method": split.get(key, {})}
            for key, figures in totals.items()
        ]

    def _window(self, statement: Any, scope: Scope, start: datetime, end: datetime) -> Any:
        statement = statement.where(RequestLog.created_at >= start, RequestLog.created_at < end)
        if scope is None:
            return statement
        if not scope:
            # No memberships and no oversight: a `where(false)` rather than a skipped query, so the
            # caller still gets a shaped empty report.
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
        # A NULL group is a real group — rows from before a column existed, requests with no use
        # case — and is labelled so it stays in the total.
        return [_figures(row.key or "(none)", row) for row in result]
