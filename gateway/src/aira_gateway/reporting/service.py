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
from aira_gateway.reporting import series as series_module

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
    #: Requests whose cost is **unknown, not zero**: nothing could be computed for them.
    unpriced_requests: int
    #: Of those, the ones where **nothing was reported to price** — the upstream answered without a
    #: token count (`FRD-626` FR-18). A different fact from "this model has no price on file", with
    #: a different remedy, and the screens said only the second until an embedding model that *has*
    #: a price turned up on one of them reading `0.00`.
    unmetered_requests: int
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
            "unmetered_requests": self.unmetered_requests,
            "failed_requests": self.failed_requests,
            "avg_latency_ms": self.avg_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


def _was_served() -> Any:
    """Whether a row describes a request that **ran**.

    NULL is a row from before `FRD-122`, when only served requests were logged. Written once because
    two counters ask it — spend that could not be computed, and usage that was never reported — and
    a refusal belongs in neither: nothing ran, so there was nothing to price or to meter, and
    counting it would make both caveats permanent for any use case with a rate limit.
    """
    return (RequestLog.outcome == Outcome.SERVED) | (RequestLog.outcome.is_(None))


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
                    (RequestLog.cost_nanos.is_(None)) & _was_served(),
                    1,
                ),
                else_=0,
            )
        ).label("unpriced_requests"),
        # The subset of the above whose cost could not be computed because the **upstream reported
        # no usage** — `total_tokens IS NULL`, which the audit writes when the adapter returned no
        # token count at all. NULL and 0 are different answers here, and the column is nullable for
        # exactly this reason: a model that genuinely used no tokens would record 0.
        func.sum(
            case(
                (
                    (RequestLog.cost_nanos.is_(None))
                    & (RequestLog.total_tokens.is_(None))
                    & _was_served(),
                    1,
                ),
                else_=0,
            )
        ).label("unmetered_requests"),
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
        unmetered_requests=int(row.unmetered_requests or 0),
        failed_requests=int(row.failed_requests or 0),
        avg_latency_ms=None if row.avg_latency_ms is None else round(float(row.avg_latency_ms)),
        max_latency_ms=None if row.max_latency_ms is None else int(row.max_latency_ms),
    )


class ReportingService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def report(
        self,
        scope: Scope,
        start: datetime,
        end: datetime,
        *,
        granularity: series_module.Granularity | None = None,
        split: str = "model",
        top: int = series_module.DEFAULT_TOP,
        person: str | None = None,
    ) -> dict[str, Any]:
        """Totals and breakdowns for ``scope`` over ``[start, end)``.

        Half-open, so reports for August and September do not both count the request that arrived
        at midnight.

        ``granularity`` adds the time series (`FRD-626`) — a bucket per day or hour, each split
        into bands. Asked for rather than always computed: the use-case overview loads this report
        twice per page for its consumption figures, and a query nobody reads is a query nobody
        notices getting slow.

        ``person`` narrows **the whole report** to one person's traffic (`FRD-626` FR-15), the same
        way ``scope`` narrows it to some use cases: one predicate on the one window every figure is
        built from, so the totals, the breakdowns and the series cannot disagree about whose traffic
        they are describing.
        """
        async with self._sessionmaker() as session:
            totals = await self._totals(session, scope, start, end, person)
            grouped = {
                name: await self._grouped(session, scope, start, end, column, person)
                for name, column in (
                    ("use_case", RequestLog.use_case),
                    ("model", RequestLog.model),
                    ("member", RequestLog.subject),
                    # Why requests ended as they did (`FRD-122` FR-8): which control refused them.
                    ("outcome", RequestLog.outcome),
                )
            }
            report: dict[str, Any] = {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "totals": totals.as_dict(),
                **{f"by_{name}": [row.as_dict() for row in rows] for name, rows in grouped.items()},
                # One person across credentials (`FRD-606`). `by_member` stays keyed on `subject`,
                # which is what every counter and budget is keyed on.
                "by_person": await self._by_person(session, scope, start, end, person),
            }
            if granularity is not None:
                report["series"] = await self._series(
                    session,
                    scope,
                    start,
                    end,
                    granularity=granularity,
                    split=split,
                    ranked=grouped[split],
                    top=top,
                    person=person,
                )
            return report

    async def _series(
        self,
        session: AsyncSession,
        scope: Scope,
        start: datetime,
        end: datetime,
        *,
        granularity: series_module.Granularity,
        split: str,
        ranked: list[Figures],
        top: int,
        person: str | None,
    ) -> dict[str, Any]:
        """The period bucket by bucket, each bucket split into bands (`FRD-626`).

        Which bands get a colour is decided from the **breakdown this report already computed** for
        the same window and scope, not from a query of its own: two rankings of one period is two
        chances for the chart's legend to disagree with the table under it.
        """
        # Re-sorted on the whole triple rather than taken as `_grouped` ordered it. That order is by
        # cost, which is the right headline — and in a period where nothing has a price on file
        # every row is zero, so the busiest band would be chosen by whatever the database happened
        # to return first.
        #
        # Negated rather than `reverse=True`, so the **name** breaks the last tie the way a reader
        # expects: A to Z. `reverse` would turn that one descending too, which is just as stable and
        # reads as an accident.
        order = sorted(
            ranked, key=lambda row: (-row.cost_nanos, -row.requests, -row.total_tokens, row.key)
        )
        kept = [row.key for row in order[:top]]
        folded = len(order) > top
        bucket = series_module.bucket(granularity)
        band = series_module.band(split, kept)

        statement = self._window(
            select(bucket.label("bucket"), band.label("key"), *_measures()),
            scope,
            start,
            end,
            person,
        )
        result = await session.execute(statement.group_by(bucket, band).order_by(bucket))
        points = [
            {
                "bucket": series_module.normalise(row.bucket, granularity),
                **_figures(row.key, row).as_dict(),
            }
            for row in result
        ]

        buckets = series_module.axis(start, end, granularity)
        # **Nothing is dropped for being off the axis.** The axis is arithmetic in this process and
        # the labels are text from the database; if they ever disagree, a column appears at the end
        # of the chart rather than a request's spend vanishing from it (`FRD-124`'s rule, one layer
        # out). A test pins the two together so this stays unreachable.
        known = set(buckets)
        buckets += sorted({point["bucket"] for point in points} - known)

        return {
            "granularity": granularity,
            "split": split,
            "buckets": buckets,
            # In the order the bands should be coloured and stacked, biggest first, with the folded
            # tail last — so a filter that changes the band count does not repaint the survivors.
            "keys": [*kept, series_module.OTHER] if folded else kept,
            "folded": folded,
            "points": points,
        }

    async def _by_person(
        self,
        session: AsyncSession,
        scope: Scope,
        start: datetime,
        end: datetime,
        person: str | None = None,
    ) -> list[dict[str, Any]]:
        """Each person's figures, with their credentials' shares shown apart and together.

        Two queries rather than one folded in Python: the totals come from the database like every
        other figure, so they cannot disagree with their parts.
        """
        totals = {
            row.key: row for row in await self._grouped(session, scope, start, end, _PERSON, person)
        }
        statement = self._window(
            select(
                _PERSON.label("key"),
                RequestLog.auth_method.label("method"),
                *_measures(),
            ),
            scope,
            start,
            end,
            person,
        )
        split: dict[str, dict[str, Any]] = {}
        for row in await session.execute(statement.group_by(_PERSON, RequestLog.auth_method)):
            key = row.key or "(none)"
            split.setdefault(key, {})[row.method or "(none)"] = _figures(key, row).as_dict()

        return [
            {**figures.as_dict(), "by_method": split.get(key, {})}
            for key, figures in totals.items()
        ]

    def _window(
        self,
        statement: Any,
        scope: Scope,
        start: datetime,
        end: datetime,
        person: str | None = None,
    ) -> Any:
        statement = statement.where(RequestLog.created_at >= start, RequestLog.created_at < end)
        if person:
            # **One predicate, on the one window.** Narrowing the report to a person anywhere else
            # would be a second place that decides what a figure is about, and the first screen to
            # use it puts that figure next to the use case's own total (`FRD-626` FR-15).
            # Spelled `__eq__` rather than `==`, and only because of the linter: `SIM300` reads a
            # column on the left of a comparison as a Yoda condition and offers to flip it. Flipped,
            # `person == _PERSON` compares a `str` with a column, answers `False`, and the filter
            # silently disappears — the suggested fix is the bug, so the call is written out.
            statement = statement.where(_PERSON.__eq__(person))
        if scope is None:
            return statement
        if not scope:
            # No memberships and no oversight: a `where(false)` rather than a skipped query, so the
            # caller still gets a shaped empty report.
            return statement.where(RequestLog.use_case.is_(None) & RequestLog.use_case.isnot(None))
        return statement.where(RequestLog.use_case.in_(scope))

    async def _totals(
        self,
        session: AsyncSession,
        scope: Scope,
        start: datetime,
        end: datetime,
        person: str | None = None,
    ) -> Figures:
        statement = self._window(select(*_measures()), scope, start, end, person)
        row = (await session.execute(statement)).one()
        return _figures("total", row)

    async def _grouped(
        self,
        session: AsyncSession,
        scope: Scope,
        start: datetime,
        end: datetime,
        column: Any,
        person: str | None = None,
    ) -> list[Figures]:
        statement = self._window(
            select(column.label("key"), *_measures()), scope, start, end, person
        )
        result = await session.execute(
            statement.group_by(column).order_by(
                func.coalesce(func.sum(RequestLog.cost_nanos), 0).desc()
            )
        )
        # A NULL group is a real group — rows from before a column existed, requests with no use
        # case — and is labelled so it stays in the total.
        return [_figures(row.key or "(none)", row) for row in result]
