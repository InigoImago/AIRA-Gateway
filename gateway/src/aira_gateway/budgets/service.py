"""Budget enforcement + usage accounting (FRD-401, FRD-403, FRD-405).

``guard`` is called pre-dispatch: it loads the budgets applicable to the request's use case +
subject and **reserves** what the request is expected to consume, refusing it with
``BudgetExceeded`` if a limit is already met. ``settle`` is called once the outcome is known: it
corrects the reservation to the real figure and books it to Postgres. ``release`` undoes the
reservation when the request never produced anything. Usage is keyed by
``(scope_key, period_key)`` so it resets naturally at each day/month boundary.

The reservation is what makes concurrency safe. Reading the usage and booking it afterwards left
a window in which every in-flight request was invisible to every other one's check, so N parallel
requests all passed a limit that only had room for one (FRD-405 §1). Reserving first closes it:
the check and the reservation are a single atomic step in the shared counter store.

Postgres remains the system of record. Redis holds a running counter seeded from it, so an outage
costs the in-flight reservations and not the period's accounting — and when it is unreachable the
service falls back to the old read-then-book path, which enforces but is racy. Refusing traffic
instead would turn a cache outage into an outage; skipping enforcement would make it free.

A budget may cap **cost**, tokens, or request count (FRD-403). Cost is the limit that answers
what a budget is normally asked, because a token differs in price by more than an order of
magnitude between models; the count limits remain available as a volume guard. Money is carried
as integer nano-units throughout — see ``aira_common.money`` for why never as a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.counters import CountersUnavailable
from aira_common.logging import get_logger
from aira_common.money import format_display
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger, Limits
from aira_gateway.db.models import BudgetRead, BudgetUsage

_log = get_logger("aira_gateway.budgets")

_BREACH_MESSAGES = {
    "cost": "Cost budget exhausted for {scope} ({period}).",
    "requests": "Request budget exhausted for {scope} ({period}).",
    "tokens": "Token budget exhausted for {scope} ({period}).",
}


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


@dataclass(slots=True)
class Reservation:
    """What a request has set aside, and what must be corrected or released afterwards.

    ``atomic`` records whether the shared counter store actually held the reservation. When it
    did not, the request was admitted by the fallback path and there is nothing to correct in
    Redis — only Postgres to book.
    """

    budgets: list[BudgetRead] = field(default_factory=list)
    reserved: Amounts = Amounts()
    period_keys: dict[int, str] = field(default_factory=dict)
    atomic: bool = False

    def __bool__(self) -> bool:
        return bool(self.budgets)


class BudgetService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        enforce: bool = True,
        ledger: BudgetLedger | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._enforce = enforce
        self._ledger = ledger

    async def guard(
        self,
        use_case: str | None,
        subject: str | None,
        now: datetime | None = None,
        *,
        estimated: Amounts | None = None,
    ) -> Reservation:
        """Reserve against the applicable budgets, or raise ``BudgetExceeded``.

        ``estimated`` is what the request is expected to consume. It cannot be exact — the cost
        depends on how many tokens the model returns — so it is corrected by :meth:`settle` the
        moment the response arrives. Erring high is the safe direction for a spend limit, and a
        request that never completes releases its reservation in full.
        """
        if not self._enforce or not use_case:
            return Reservation()
        now = now or datetime.now(UTC)
        amounts = estimated or Amounts(requests=1)
        async with self._sessionmaker() as session:
            budgets = await self._applicable(session, use_case, subject)
            if not budgets:
                return Reservation()
            if self._ledger is not None:
                try:
                    return await self._reserve(session, budgets, amounts, now)
                except CountersUnavailable:
                    # Enforce anyway, the old way: racy, but the alternatives are refusing all
                    # traffic or handing out free spend while a cache is down (FRD-405 §4.3).
                    _log.warning("budget_reservation_degraded", use_case=use_case)
            await self._check_only(session, budgets, now)
        return Reservation(budgets=budgets, atomic=False)

    async def _reserve(
        self,
        session: AsyncSession,
        budgets: list[BudgetRead],
        amounts: Amounts,
        now: datetime,
    ) -> Reservation:
        """Atomically reserve against every applicable budget.

        A budget that refuses undoes the reservations already made for *this* request before
        raising. Leaving them in place would let a refused request permanently consume headroom
        on the budgets it did clear.
        """
        assert self._ledger is not None
        reservation = Reservation(budgets=budgets, reserved=amounts, atomic=True)
        for budget in budgets:
            scope_key = _scope_key(budget)
            period_key = _period_key(budget.period, now)
            seed = await self._usage(session, scope_key, period_key)
            breached = await self._ledger.reserve(
                scope_key,
                period_key,
                budget.period,
                limits=Limits(
                    tokens=budget.limit_tokens,
                    requests=budget.limit_requests,
                    cost_nanos=budget.limit_cost_nanos,
                ),
                amounts=amounts,
                seed=Amounts(seed.tokens, seed.requests, seed.cost_nanos),
            )
            if breached:
                await self.release(reservation)
                raise BudgetExceeded(
                    _BREACH_MESSAGES[breached].format(scope=budget.scope, period=budget.period)
                )
            reservation.period_keys[budget.id] = period_key
        return reservation

    async def _check_only(
        self, session: AsyncSession, budgets: list[BudgetRead], now: datetime
    ) -> None:
        """The pre-FRD-405 path: read the usage and refuse if a limit is already met.

        Used when no shared counter store is configured or it cannot be reached. It enforces,
        but concurrent requests remain invisible to each other — which is the whole reason the
        reservation path exists.
        """
        for budget in budgets:
            usage = await self._usage(session, _scope_key(budget), _period_key(budget.period, now))
            breached = None
            if budget.limit_cost_nanos is not None and usage.cost_nanos >= budget.limit_cost_nanos:
                breached = "cost"
            elif budget.limit_requests is not None and usage.requests >= budget.limit_requests:
                breached = "requests"
            elif budget.limit_tokens is not None and usage.tokens >= budget.limit_tokens:
                breached = "tokens"
            if breached:
                raise BudgetExceeded(
                    _BREACH_MESSAGES[breached].format(scope=budget.scope, period=budget.period)
                )

    async def settle(
        self,
        reservation: Reservation,
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Book the real figure: correct the reservation and persist it.

        Postgres receives the actual consumption; the shared counter is moved by the difference
        between what was reserved and what was really used, so it converges on the same total.
        """
        if not reservation.budgets:
            return
        now = now or datetime.now(UTC)
        await self.record(reservation.budgets, tokens, cost_nanos=cost_nanos, now=now)
        if not reservation.atomic or self._ledger is None:
            return
        actual = Amounts(tokens=tokens, requests=1, cost_nanos=cost_nanos or 0)
        correction = Amounts(
            tokens=actual.tokens - reservation.reserved.tokens,
            requests=actual.requests - reservation.reserved.requests,
            cost_nanos=actual.cost_nanos - reservation.reserved.cost_nanos,
        )
        await self._move(reservation, correction)

    async def release(self, reservation: Reservation) -> None:
        """Give a reservation back — the request produced nothing to charge for.

        Without this an upstream failure would consume budget permanently, so a provider outage
        would look to a use case exactly like having spent its month.
        """
        if not reservation.atomic or self._ledger is None:
            return
        await self._move(reservation, reservation.reserved.negated())

    async def _move(self, reservation: Reservation, amounts: Amounts) -> None:
        assert self._ledger is not None
        for budget in reservation.budgets:
            period_key = reservation.period_keys.get(budget.id)
            if period_key is None:
                continue  # never reserved against this budget (the one that refused)
            try:
                await self._ledger.adjust(
                    _scope_key(budget), period_key, budget.period, amounts=amounts
                )
            except CountersUnavailable:
                # Postgres already holds the truth; the counter reseeds from it on the next miss.
                _log.warning("budget_adjust_degraded", budget_id=budget.id)

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
