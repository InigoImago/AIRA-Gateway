"""Budget enforcement and usage accounting (`FRD-401`, `FRD-403`, `FRD-405`).

- :meth:`BudgetService.guard` runs pre-dispatch: it **reserves** what the request is expected to
  consume against every budget that binds it, or raises :class:`BudgetExceeded`.
- :meth:`~BudgetService.settle` corrects the reservation to the real figure and books it to
  Postgres; :meth:`~BudgetService.release` hands it back when nothing was produced.

Reserving first is what makes concurrency safe: check and reservation are one atomic step in the
shared counter store (`budgets.ledger`), so in-flight requests see each other (`FRD-405` §1).
Postgres stays the system of record (`budgets.store`); when Redis is unreachable the service falls
back to read-then-book, which enforces but is racy — refusing all traffic would turn a cache outage
into an outage, and skipping enforcement would make it free.

A budget may cap cost, tokens or requests (`FRD-403`). Money is integer nano-units throughout.
Counters are keyed by ``(scope_key, period_key)`` and reset at each UTC day or month
(`budgets.keys`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.counters import CountersUnavailable, DegradationLog
from aira_common.logging import get_logger
from aira_common.money import format_display
from aira_gateway.budgets import keys
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger, Limits
from aira_gateway.budgets.store import accumulate, applicable, read_usage
from aira_gateway.db.models import BudgetRead
from aira_gateway.scopes import EACH_MEMBER

__all__ = ["Amounts", "BudgetExceeded", "BudgetService", "Reservation"]

_log = get_logger("aira_gateway.budgets")


@dataclass(slots=True)
class Reservation:
    """What a request has set aside, and what must be corrected or released afterwards.

    ``atomic`` records whether the shared counter store held the reservation; when it did not, the
    request was admitted by the fallback path and only Postgres is booked. ``resolved`` records
    that the outcome was accounted for, by settling or releasing — :meth:`BudgetService.hold` uses
    it so no exit path leaves a reservation behind, and a second resolution is a no-op.
    """

    budgets: list[BudgetRead] = field(default_factory=list)
    #: Who the request is from: an `each_member` budget's counter key **is** the caller, and
    #: settle/release run long after the subject was resolved.
    subject: str | None = None
    #: What this request set aside, so `settle` can correct it and `release` can hand it back.
    reserved: Amounts = Amounts()
    period_keys: dict[int, str] = field(default_factory=dict)
    atomic: bool = False
    resolved: bool = False

    def __bool__(self) -> bool:
        return bool(self.budgets)


class BudgetService:
    FEATURE = "budget enforcement"

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        enforce: bool = True,
        ledger: BudgetLedger | None = None,
        degradation: DegradationLog | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._enforce = enforce
        self._ledger = ledger
        # `is not None`, not `or`: an empty log is falsy and `or` would discard the caller's.
        self._degradation = degradation if degradation is not None else DegradationLog()

    # == before dispatch ==========================================================================

    async def guard(
        self,
        use_case: str | None,
        subject: str | None,
        now: datetime | None = None,
        *,
        estimated: Amounts | None = None,
    ) -> Reservation:
        """Reserve against the applicable budgets, or raise ``BudgetExceeded``.

        ``estimated`` cannot be exact — cost depends on the answer — so :meth:`settle` corrects
        it; erring high is the safe direction for a spend limit. A request naming no use case is
        **not exempt**: it books against the installation budget, where one exists (`FRD-610`).
        """
        if not self._enforce:
            return Reservation()
        now = now or datetime.now(UTC)
        amounts = estimated or Amounts(requests=1)
        async with self._sessionmaker() as session:
            budgets = await applicable(session, use_case, subject)
            if not budgets:
                return Reservation()
            if self._ledger is not None:
                partial = Reservation(
                    budgets=budgets,
                    subject=subject,
                    reserved=amounts,
                    atomic=True,
                )
                try:
                    reserved = await self._reserve(session, partial, now)
                    self._degradation.working(self.FEATURE)
                    return reserved
                except CountersUnavailable:
                    # Redis may have gone between two budgets: hand back the ones already
                    # reserved, which nothing downstream holds a reference to.
                    await self.release(partial)
                    # Enforce anyway, the racy way (`FRD-405` §4.3).
                    _log.warning("budget_reservation_degraded", use_case=use_case)
                    self._degradation.degraded(
                        self.FEATURE,
                        "Postgres read-then-book; concurrent requests can overshoot a limit",
                    )
            await self._check_only(session, budgets, now, subject)
        return Reservation(budgets=budgets, subject=subject, atomic=False)

    async def _reserve(
        self, session: AsyncSession, reservation: Reservation, now: datetime
    ) -> Reservation:
        """Atomically reserve against every applicable budget.

        A budget that refuses first undoes this request's reservations on the others, or a refused
        request would keep consuming their headroom. The reservation is passed in, not created
        here, so the caller still holds it if this raises part-way through.
        """
        assert self._ledger is not None
        amounts = reservation.reserved
        for budget in reservation.budgets:
            scope_key = keys.scope_key(budget, reservation.subject)
            period_key = keys.period_key(budget.period, now)
            seed = await read_usage(session, scope_key, period_key)
            breached = await self._ledger.reserve(
                scope_key,
                period_key,
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
                raise keys.exceeded(budget, breached)
            reservation.period_keys[budget.id] = period_key
        reservation.resolved = False  # reserved; the outcome is still open
        return reservation

    async def _check_only(
        self,
        session: AsyncSession,
        budgets: list[BudgetRead],
        now: datetime,
        caller: str | None = None,
    ) -> None:
        """Read the usage and refuse if a limit is already met.

        The path without a reachable counter store: it enforces, but concurrent requests stay
        invisible to each other — the reason the reservation exists.
        """
        for budget in budgets:
            usage = await read_usage(
                session, keys.scope_key(budget, caller), keys.period_key(budget.period, now)
            )
            breached = None
            if budget.limit_cost_nanos is not None and usage.cost_nanos >= budget.limit_cost_nanos:
                breached = "cost"
            elif budget.limit_requests is not None and usage.requests >= budget.limit_requests:
                breached = "requests"
            elif budget.limit_tokens is not None and usage.tokens >= budget.limit_tokens:
                breached = "tokens"
            if breached:
                raise keys.exceeded(budget, breached)

    async def refuse_if_exhausted(
        self,
        use_case: str | None,
        subject: str | None,
        now: datetime | None = None,
    ) -> None:
        """Refuse a use case that is **already** over a limit, before anything is spent on it.

        Not a reservation and no substitute for :meth:`guard`: it answers the cheaper question
        *has this use case spent its allowance?*, which needs no model and so can be asked before
        routing and the pipeline — whose classifier calls would otherwise be billed for requests
        that are then refused.
        """
        # No use case, no pipeline to protect (`FRD-125c`); `guard` bounds unattributed spend.
        if not use_case:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = await applicable(session, use_case, subject)
            if budgets:
                await self._check_only(session, budgets, now, subject)

    # == after dispatch ===========================================================================

    @asynccontextmanager
    async def hold(self, reservation: Reservation) -> AsyncIterator[Reservation]:
        """Guarantee that a reservation is resolved, whatever happens inside the block.

        Structural rather than a release at each failure site, so a malformed upstream body, a
        database hiccup or a future exit path cannot leave a reservation behind.
        """
        try:
            yield reservation
        finally:
            if not reservation.resolved:
                try:
                    await self.release(reservation)
                except Exception as exc:  # cleanup must not replace the original failure
                    _log.error(
                        "budget_release_failed", error=str(exc), error_type=type(exc).__name__
                    )

    async def settle(
        self,
        reservation: Reservation,
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
        requests: int = 1,
    ) -> None:
        """Book the real figure: persist it, and move the shared counter by the difference.

        ``requests`` is what the call weighed — one, or one **per text** of an embedding batch
        (`FRD-113` FR-6), or a request-count budget could not see batched traffic.
        """
        reservation.resolved = True
        if not reservation.budgets:
            return
        now = now or datetime.now(UTC)
        await self.record(
            reservation.budgets,
            tokens,
            cost_nanos=cost_nanos,
            now=now,
            requests=requests,
            subject=reservation.subject,
        )
        if not reservation.atomic or self._ledger is None:
            return
        actual = Amounts(tokens=tokens, requests=requests, cost_nanos=cost_nanos or 0)
        correction = Amounts(
            tokens=actual.tokens - reservation.reserved.tokens,
            requests=actual.requests - reservation.reserved.requests,
            cost_nanos=actual.cost_nanos - reservation.reserved.cost_nanos,
        )
        await self._move(reservation, correction)

    async def release(self, reservation: Reservation) -> None:
        """Give a reservation back — the request produced nothing to charge for.

        Otherwise a provider outage would look to a use case exactly like having spent its month.
        """
        reservation.resolved = True
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
                    keys.scope_key(budget, reservation.subject),
                    period_key,
                    amounts=amounts,
                )
            except CountersUnavailable:
                # The counter keeps this request's estimate until it expires and is rebuilt from
                # Postgres, which has the settled figure (`COUNTER_TTL_SECONDS`).
                _log.warning("budget_adjust_degraded", budget_id=budget.id)

    async def record(
        self,
        budgets: list[BudgetRead],
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
        requests: int = 1,
        subject: str | None = None,
    ) -> None:
        """Book a request — or a batch counted as the many it is — against every budget.

        Keyword-only, so an amount of money and a timestamp cannot be swapped as positionals.
        ``cost_nanos`` of ``None`` is counted as unpriced, not as free (`FRD-403`). ``subject`` is
        required by a per-person budget, whose counter key is the caller.
        """
        if not budgets:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            for budget in budgets:
                await session.execute(
                    accumulate(
                        session,
                        scope_key=keys.scope_key(budget, subject),
                        period_key=keys.period_key(budget.period, now),
                        tokens=tokens,
                        requests=requests,
                        cost_nanos=cost_nanos,
                    )
                )
            await session.commit()

    async def book_side_call(
        self,
        use_case: str | None,
        subject: str | None,
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Book tokens the **gateway** spent on the caller's behalf — a pipeline step (`FRD-125`).

        - **Counted as a request**, by the owner's decision: a step's call reaches a model and costs
          money, and a request budget that cannot see it is sized for other work. **Rate limits
          are untouched** — a bucket measures how fast requests arrive, and the gate is taken once.
        - Not a reservation: the tokens are spent before their size is known, because the pipeline
          runs before routing picks the model the reservation is made against.
        - **Both stores**: the guard reads the shared counter, so booking Postgres alone would let
          the spend reach enforcement only when the counter is rebuilt.
        """
        # Unattributed traffic has no pipeline, so there is nothing here to book.
        if not use_case or tokens <= 0:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = await applicable(session, use_case, subject)
        await self.record(
            budgets,
            tokens,
            cost_nanos=cost_nanos,
            now=now,
            requests=1,
            subject=subject,
        )
        if self._ledger is None:
            return  # degraded to the Postgres path, which the line above already wrote
        amounts = Amounts(tokens=tokens, requests=1, cost_nanos=cost_nanos or 0)
        for budget in budgets:
            try:
                await self._ledger.adjust(
                    keys.scope_key(budget, subject),
                    keys.period_key(budget.period, now),
                    amounts=amounts,
                )
            except CountersUnavailable:
                # Postgres has it and the counter rebuilds from Postgres; until then the counter is
                # low, so a caller is under-charged rather than refused.
                _log.warning("budget_side_call_adjust_degraded", budget_id=budget.id)

    # == reporting ================================================================================

    async def usage(
        self,
        use_case: str,
        now: datetime | None = None,
        *,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Current-period usage per budget of a use case, for the consumption view (`FRD-402`).

        Cost comes as a decimal string for people and as nano-units for a progress bar. A
        **per-person** budget is one counter per head, so ``measured_for`` names whose figure it
        is; a reader it does not bind gets ``None`` for every figure rather than a zero, which is
        also what an untouched allowance looks like (`FRD-603`).
        """
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(BudgetRead).where(BudgetRead.use_case == use_case)
            )
            out: list[dict[str, Any]] = []
            for budget in result.scalars():
                measured_for = subject if budget.scope == EACH_MEMBER else budget.subject
                row: dict[str, Any] = {"id": budget.id, "measured_for": measured_for}
                if budget.scope == EACH_MEMBER and not subject:
                    row.update(
                        used_tokens=None,
                        used_requests=None,
                        used_cost_nanos=None,
                        used_cost=None,
                        unpriced_requests=None,
                    )
                    out.append(row)
                    continue
                # Only `each_member` depends on the reader. Any other row resolves to its own
                # subject; passing the reader as its caller would fail `scope_key`'s assertion.
                caller = subject if budget.scope == EACH_MEMBER else None
                usage = await read_usage(
                    session,
                    keys.scope_key(budget, caller),
                    keys.period_key(budget.period, now),
                )
                row.update(
                    used_tokens=usage.tokens,
                    used_requests=usage.requests,
                    used_cost_nanos=usage.cost_nanos,
                    used_cost=format_display(usage.cost_nanos),
                    unpriced_requests=usage.unpriced_requests,
                )
                out.append(row)
            return out
