"""Spend and usage aggregates over the request log (FRD-601).

Every dispatched request has been recorded since FRD-103 and priced since FRD-403; this is what
finally reads it. The figures are aggregated **in the database**: pulling a period's rows into the
process to sum them would move the whole window across the wire to compute four numbers from it,
and would grow with traffic the installation does not control.

Two rules carry over from the budget work and matter as much here:

- **Unpriced is not zero, and zero is not unknown.** A request *served* on a model with no price
  counts toward ``unpriced_requests``
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
from aira_gateway.audit import PIPELINE_OPERATION_PREFIX, Outcome
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
    #: Of the prompt tokens, how many came from a provider's cache (`FRD-133` FR-5). Reported
    #: because a cache that has silently stopped working looks exactly like an expensive month —
    #: the spend rises, nothing errors, and no figure says why.
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
            # Both forms, the same pair FRD-403 established: the string is what a human reads,
            # the integer is what a bar divides without a float touching a monetary figure.
            "cost_nanos": self.cost_nanos,
            "cost": format_display(self.cost_nanos),
            "unpriced_requests": self.unpriced_requests,
            "failed_requests": self.failed_requests,
            "avg_latency_ms": self.avg_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


_EMPTY = Figures("", 0, 0, 0, 0, 0, 0, 0, 0, None, None)


def _measures() -> list[Any]:
    """The columns every breakdown reports, in one place so the rows cannot diverge."""
    return [
        # **A caller's own requests**, which is not the same as rows. A pipeline step's model call
        # is an audit row of its own (`FRD-125` FR-8) and is deliberately *not* a request: FR-9
        # books it with `requests=0` because "the caller made one request and counting the
        # classifier as a second would inflate every request figure". The budgets honoured that and
        # this measure did not, so a use case running an LLM filter and a router reported two to
        # three times the traffic it received — in the figures a governance role reads to decide
        # whether a control is working.
        #
        # Only the **count** narrows. The tokens and the money below still sum every row, because
        # those rows exist precisely so that what governing a use case costs is visible (FR-8);
        # excluding them from the spend would trade one wrong figure for another.
        func.count()
        .filter(
            (RequestLog.operation.is_(None))
            | (RequestLog.operation.not_like(f"{PIPELINE_OPERATION_PREFIX}%"))
        )
        .label("requests"),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(RequestLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(RequestLog.cost_nanos), 0).label("cost_nanos"),
        # A request whose model had no price. Counted, never summed as zero.
        # Unpriced means **served on a model with no price**, not merely "no cost recorded".
        #
        # A refused request also has a NULL cost, and for the opposite reason: nothing was spent,
        # because nothing ran. Counting both made the console report 105 unpriced requests where
        # 5 had actually run unpriced — and it made the "spend is a lower bound" caveat permanent,
        # since every installation refuses some traffic and a warning that is always there is one
        # nobody reads. Found by looking at the figure after a live round produced refusals.
        #
        # The project's own rule, applied in the direction it was missing: unknown is not zero,
        # and **zero is not unknown**.
        func.sum(
            case(
                (
                    (RequestLog.cost_nanos.is_(None))
                    & (
                        (RequestLog.outcome == Outcome.SERVED)
                        # A NULL outcome is a row from before `FRD-122`, when only *served*
                        # requests were logged at all — so it was one, and excluding it would
                        # quietly change a historical figure.
                        | (RequestLog.outcome.is_(None))
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("unpriced_requests"),
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
                # One person, and how they authenticated (`FRD-606` FR-2/FR-3). `by_member` above
                # stays as it is: it groups by `subject`, which is what every counter and every
                # budget is keyed on, and a report that quietly changed that key would answer a
                # different question than the one enforcement asks.
                "by_person": await self._by_person(session, scope, start, end),
            }

    async def _by_person(
        self, session: AsyncSession, scope: Scope, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Each person's figures, with the two credentials shown apart and together.

        Two queries rather than one grouped by both columns and folded in Python: the totals must
        come from the database for the same reason every other figure does — a sum assembled from
        parts is a sum that disagrees with the parts the first time one of them is filtered out.
        """
        totals = {
            row.key: row for row in await self._grouped(session, scope, start, end, self._PERSON)
        }
        statement = self._window(
            select(
                self._PERSON.label("key"),
                RequestLog.auth_method.label("method"),
                *_measures(),
            ),
            scope,
            start,
            end,
        )
        split: dict[str, dict[str, Any]] = {}
        for row in await session.execute(statement.group_by(self._PERSON, RequestLog.auth_method)):
            key = row.key or "(none)"
            split.setdefault(key, {})[row.method or "(none)"] = _figures(key, row).as_dict()

        return [
            {**figures.as_dict(), "by_method": split.get(key, {})}
            for key, figures in totals.items()
        ]

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

    #: How a **person** is grouped (`FRD-606`).
    #:
    #: The name where the credential carried one, the subject otherwise. That is the only join
    #: there is: an OIDC token's subject is the directory's user id and an API key's is its
    #: owner's username, so grouping by subject alone reports one person as two rows and labels
    #: neither. `subject` remains the identity everywhere it matters — this is a display.
    #:
    #: A row written before the column existed has no name and stands alone, which is the honest
    #: answer for it: the join genuinely was not recorded.
    _PERSON = func.coalesce(RequestLog.username, RequestLog.subject)

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
