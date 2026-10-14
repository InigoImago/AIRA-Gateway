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
from aira_gateway.scopes import EACH_MEMBER, Scope
from aira_gateway.shielding import uninterrupted

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
    #: Budgets that bind and refuse nothing — lifted (`enabled` false). Never reserved against;
    #: booked when the request settles, so their figures stay true (`store.applicable`).
    lifted: list[BudgetRead] = field(default_factory=list)
    #: Who the request is from: an `each_member` budget's counter key **is** the caller, and
    #: settle/release run long after the subject was resolved.
    subject: str | None = None
    #: What this request set aside, so `settle` can correct it and `release` can hand it back.
    reserved: Amounts = Amounts()
    period_keys: dict[int, str] = field(default_factory=dict)
    atomic: bool = False
    resolved: bool = False

    def __bool__(self) -> bool:
        return bool(self.budgets or self.lifted)


def _left(limit: int | None, used: int) -> int | None:
    """What is left of one capped dimension, or ``None`` where it is not capped.

    ``None`` and not a large number: *"this budget does not limit tokens"* and *"you have a lot of
    tokens left"* are different answers, and a client that renders the second as a full bar would
    be inventing a limit nobody set.
    """
    return None if limit is None else limit - used


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

        **With enforcement off it reserves nothing and refuses nobody — and still counts**: the
        reservation names the budgets, so :meth:`settle` books the request like any other. The
        figures stay true, and a limit switched back on starts from what was actually spent.
        """
        now = now or datetime.now(UTC)
        amounts = estimated or Amounts(requests=1)
        async with self._sessionmaker() as session:
            binding = await applicable(session, use_case, subject)
            if not binding:
                return Reservation()
            # Each budget is booked under the period it was admitted in, whenever the request ends.
            periods = {budget.id: keys.period_key(budget.period, now) for budget in binding}
            budgets = [budget for budget in binding if budget.enabled]
            lifted = [budget for budget in binding if not budget.enabled]
            if not self._enforce:
                return Reservation(
                    budgets=budgets, lifted=lifted, subject=subject, period_keys=periods
                )
            if self._ledger is not None:
                partial = Reservation(
                    budgets=budgets,
                    lifted=lifted,
                    subject=subject,
                    reserved=amounts,
                    # The lifted budgets' periods now; the enforced ones' as each is reserved.
                    period_keys={budget.id: periods[budget.id] for budget in lifted},
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
                except BaseException:
                    # Anything else part-way through — a database error reading the next budget's
                    # figure, a cancellation — leaves the budgets already reserved holding headroom
                    # nothing will settle. Handed back; the failure itself goes on.
                    if not partial.resolved:
                        await self._release_quietly(partial)
                    raise
            await self._check_only(session, budgets, now, subject)
        return Reservation(
            budgets=budgets, lifted=lifted, subject=subject, period_keys=periods, atomic=False
        )

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
        # With enforcement off nothing is refused, however far over a limit the figures are.
        if not use_case or not self._enforce:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = [b for b in await applicable(session, use_case, subject) if b.enabled]
            if budgets:
                await self._check_only(session, budgets, now, subject)

    # == after dispatch ===========================================================================

    @asynccontextmanager
    async def hold(self, reservation: Reservation) -> AsyncIterator[Reservation]:
        """Guarantee that a reservation is resolved, whatever happens inside the block.

        Structural rather than a release at each failure site, so a malformed upstream body, a
        database hiccup or a future exit path cannot leave a reservation behind. The release runs
        to its end even when the block was left by a cancellation (`shielding.uninterrupted`): a
        dropped connection is when a reservation most needs giving back, and a release cancelled
        at its first await would leave it in the counter, refreshed by every reservation after it.
        """
        try:
            yield reservation
        finally:
            if not reservation.resolved:
                await self._release_quietly(reservation)

    async def _release_quietly(self, reservation: Reservation) -> None:
        """Release ``reservation`` to the end, and log rather than raise if that fails: this runs
        while another failure is on its way out, and must not replace it."""
        try:
            await uninterrupted(self.release(reservation))
        except Exception as exc:  # cleanup must not replace the original failure
            _log.error("budget_release_failed", error=str(exc), error_type=type(exc).__name__)

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
        if not reservation:
            return
        now = now or datetime.now(UTC)
        # Under the period the request was admitted in, in both stores: one admitted at 23:59:59
        # and settled after midnight belongs to the counter it reserved against — booked into the
        # next period instead, neither period's counter would ever see it.
        await self.record(
            [*reservation.budgets, *reservation.lifted],
            tokens,
            cost_nanos=cost_nanos,
            now=now,
            requests=requests,
            subject=reservation.subject,
            periods=reservation.period_keys,
        )
        actual = Amounts(tokens=tokens, requests=requests, cost_nanos=cost_nanos or 0)
        # A lifted budget reserved nothing, so its shared counter takes the whole figure — or a
        # budget put back within the counter's lifetime would start from less than was spent.
        await self._add_to_counters(
            reservation.lifted, reservation.subject, reservation.period_keys, actual, now
        )
        if not reservation.atomic or self._ledger is None:
            return
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
        periods: dict[int, str] | None = None,
    ) -> None:
        """Book a request — or a batch counted as the many it is — against every budget.

        Keyword-only, so an amount of money and a timestamp cannot be swapped as positionals.
        ``cost_nanos`` of ``None`` is counted as unpriced, not as free (`FRD-403`). ``subject`` is
        required by a per-person budget, whose counter key is the caller. ``periods`` names the
        period a budget was reserved in; one it does not name is booked under ``now``'s.
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
                        period_key=(periods or {}).get(budget.id)
                        or keys.period_key(budget.period, now),
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
        amounts = Amounts(tokens=tokens, requests=1, cost_nanos=cost_nanos or 0)
        await self._add_to_counters(budgets, subject, {}, amounts, now)

    async def _add_to_counters(
        self,
        budgets: list[BudgetRead],
        subject: str | None,
        periods: dict[int, str],
        amounts: Amounts,
        now: datetime,
    ) -> None:
        """Add a figure no reservation held to each budget's shared counter.

        **Both stores** for what was booked in Postgres without a reservation — a step's call, a
        lifted budget: the guard reads the shared counter, so booking Postgres alone would let the
        spend reach enforcement only when the counter is rebuilt.
        """
        if self._ledger is None:
            return  # degraded to the Postgres path, which the caller already wrote
        for budget in budgets:
            try:
                await self._ledger.adjust(
                    keys.scope_key(budget, subject),
                    periods.get(budget.id) or keys.period_key(budget.period, now),
                    amounts=amounts,
                )
            except CountersUnavailable:
                # Postgres has it and the counter rebuilds from Postgres; until then the counter is
                # low, so a caller is under-charged rather than refused.
                _log.warning("budget_side_call_adjust_degraded", budget_id=budget.id)

    # == reporting ================================================================================

    async def cost_limited(self, use_case: str | None, subject: str | None) -> bool:
        """Whether a **cost** limit binds this request (`FRD-610` §3.2).

        Asked only when a model turns out to have no price, so the ordinary request pays for no
        extra query. The same `applicable` rows the reservation books against, so the question
        *"is spend capped here"* is answered by the thing that would do the capping — a second
        definition of which budgets bind is the copy that disagrees.

        With enforcement off nothing is capped at all, and the answer is `False`: refusing a model
        to protect a limit that refuses nothing would be the strictest reading of the least
        meaningful configuration.
        """
        if not self._enforce:
            return False
        async with self._sessionmaker() as session:
            budgets = await applicable(session, use_case, subject)
        # A lifted budget caps nothing, so it binds no model to a price either.
        return any(budget.enabled and budget.limit_cost_nanos is not None for budget in budgets)

    async def usage(
        self,
        use_case: str,
        now: datetime | None = None,
        *,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Current-period usage per budget of a use case, for the consumption view (`FRD-402`) and
        for a caller asking about their own allowance (`FRD-139`).

        Cost comes as a decimal string for people and as nano-units for a progress bar. A
        **per-person** budget is one counter per head, so ``measured_for`` names whose figure it
        is; a reader it does not bind gets ``None`` for every figure rather than a zero, which is
        also what an untouched allowance looks like (`FRD-603`).

        Each row also carries **what the limit is and what is left of it**, so that one answer is
        enough for the question people actually ask. Three dimensions, each capped or not: a
        ``limit_*`` of ``None`` means *this budget does not cap that dimension*, and the matching
        ``remaining_*`` is ``None`` too — not a large number, and not zero.

        ``remaining_*`` may be **negative**, and is reported as it is. Enforcement refuses at the
        limit, so going past it takes a settle correcting a reservation upward; clamping to zero
        would hide exactly that and make "spent to the last cent" indistinguishable from "spent
        more than the budget allowed". A display may clamp; the figure does not.
        """
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(BudgetRead).where(BudgetRead.use_case == use_case)
            )
            out: list[dict[str, Any]] = []
            for budget in result.scalars():
                # Only `each_member` depends on the reader; any other row resolves to its own scope.
                caller = subject if budget.scope == EACH_MEMBER else None
                scope = Scope.applying(scope=budget.scope, use_case=budget.use_case, caller=caller)
                measured_for = subject if budget.scope == EACH_MEMBER else budget.subject
                row: dict[str, Any] = {
                    "id": budget.id,
                    "measured_for": measured_for,
                    "scope": budget.scope,
                    "period": budget.period,
                    # When this counter is replaced by the next one — the moment an exhausted
                    # allowance comes back, which is the second thing a refused caller asks.
                    "period_ends": keys.period_ends(budget.period, now).isoformat(),
                    "enabled": budget.enabled,
                    "limit_cost_nanos": budget.limit_cost_nanos,
                    "limit_cost": (
                        None
                        if budget.limit_cost_nanos is None
                        else format_display(budget.limit_cost_nanos)
                    ),
                    "limit_tokens": budget.limit_tokens,
                    "limit_requests": budget.limit_requests,
                }
                if scope is None:
                    # No counter to read: a per-person budget asked about by nobody, or a scope this
                    # build does not know — reported as stored, with no figures, rather than a 500
                    # for every budget of the use case. Enforcement skips the same row.
                    row.update(
                        used_tokens=None,
                        used_requests=None,
                        used_cost_nanos=None,
                        used_cost=None,
                        unpriced_requests=None,
                        remaining_cost_nanos=None,
                        remaining_cost=None,
                        remaining_tokens=None,
                        remaining_requests=None,
                    )
                    out.append(row)
                    continue
                usage = await read_usage(
                    session, scope.usage_key, keys.period_key(budget.period, now)
                )
                left_cost = _left(budget.limit_cost_nanos, usage.cost_nanos)
                row.update(
                    used_tokens=usage.tokens,
                    used_requests=usage.requests,
                    used_cost_nanos=usage.cost_nanos,
                    used_cost=format_display(usage.cost_nanos),
                    unpriced_requests=usage.unpriced_requests,
                    remaining_cost_nanos=left_cost,
                    remaining_cost=(None if left_cost is None else format_display(left_cost)),
                    remaining_tokens=_left(budget.limit_tokens, usage.tokens),
                    remaining_requests=_left(budget.limit_requests, usage.requests),
                )
                out.append(row)
            return out
