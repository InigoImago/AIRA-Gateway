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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.counters import CountersUnavailable, DegradationLog
from aira_common.logging import get_logger
from aira_common.money import format_display
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger, Limits
from aira_gateway.db.models import BudgetRead, BudgetUsage
from aira_gateway.scopes import EACH_MEMBER, USE_CASE, Scope

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
    """The counter key for this moment: the **UTC** calendar day or month.

    One clock for an installation whose callers, models and operators sit in several, and a couple
    of hours from the reader's own calendar in both directions — in central Europe, traffic at
    00:30 local counts against yesterday, and a monthly budget that ran out goes on refusing for
    the first hours of the new month. The console's Period control says so.

    **The conversion is here rather than assumed of the caller.** This used to `strftime` whatever
    it was handed, which is UTC only because every call site happens to pass `datetime.now(UTC)` —
    a guarantee held by four call sites instead of by the one function that states it, and the
    kind that a fifth quietly breaks. `astimezone(UTC)` is a no-op for an already-UTC moment, so
    nothing changes today and the property stops depending on who calls.
    """
    moment = now.astimezone(UTC)
    return moment.strftime("%Y-%m-%d") if period == "day" else moment.strftime("%Y-%m")


def _scope_label(budget: BudgetRead) -> str:
    """How the exhausted budget is named to the caller.

    The stored value is a configuration word; a refusal is read by somebody who did not write the
    configuration. `each_member` in particular would be reported to a caller as the name of a
    setting rather than as what happened to them, which is that their own allowance is gone.
    """
    return "use case" if budget.scope == USE_CASE else "member"


def _scope_key(budget: BudgetRead, caller: str | None = None) -> str:
    """The key this budget's consumption is accounted under.

    Applicability has already been decided by :meth:`_applicable`; this only asks under which key
    the figures live.
    """
    scope = Scope.applying(
        scope=budget.scope,
        use_case=budget.use_case,
        # `each_member` names nobody, so **the caller is the key** — one configured row, one
        # counter per head. Reading the key off the row was right while every scope identified
        # itself, and became a silent hole the moment one did not.
        caller=caller,
    )
    assert scope is not None, (
        f"budget {budget.id} ({budget.scope}) does not bind caller {caller!r} — it should never "
        "have reached here, since _applicable resolves the same question"
    )
    return scope.usage_key


@dataclass(slots=True)
class Reservation:
    """What a request has set aside, and what must be corrected or released afterwards.

    ``atomic`` records whether the shared counter store actually held the reservation. When it
    did not, the request was admitted by the fallback path and there is nothing to correct in
    Redis — only Postgres to book.

    ``resolved`` records that the outcome has been accounted for, by either settling or
    releasing. :meth:`BudgetService.hold` uses it to guarantee that no exit path can leave a
    reservation behind, and it also makes a double resolution a no-op.
    """

    budgets: list[BudgetRead] = field(default_factory=list)
    #: Who the request is from. Carried because a `each_member` budget's counter key **is** the
    #: caller, and settle/release run long after the subject was resolved.
    subject: str | None = None
    #: The name that subject is known by, where the credential carries one. Carried for the same
    #: reason as `subject`: a member row may have matched on it, and settle/release run long
    #: after the caller was resolved.
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
        # See the note in ratelimit/buckets.py: an empty log is falsy, so `or` would discard
        # the caller's log every time.
        self._degradation = degradation if degradation is not None else DegradationLog()

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
                    # Redis may have gone away *between* two budgets, leaving the ones already
                    # reserved holding a request that nothing downstream still has a reference
                    # to. Handing them back is what stops a counter being inflated by a request
                    # that will never be settled or released.
                    await self.release(partial)
                    # Enforce anyway, the old way: racy, but the alternatives are refusing all
                    # traffic or handing out free spend while a cache is down (FRD-405 §4.3).
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

        A budget that refuses undoes the reservations already made for *this* request before
        raising. Leaving them in place would let a refused request permanently consume headroom
        on the budgets it did clear.

        The reservation is passed in rather than created here so the caller still holds it if
        this raises part-way through — otherwise the reservations already made become
        unreachable, which is exactly how a counter ends up inflated with nobody able to clear it.
        """
        assert self._ledger is not None
        amounts = reservation.reserved
        for budget in reservation.budgets:
            scope_key = _scope_key(budget, reservation.subject)
            period_key = _period_key(budget.period, now)
            seed = await self._usage(session, scope_key, period_key)
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
                raise BudgetExceeded(
                    _BREACH_MESSAGES[breached].format(
                        scope=_scope_label(budget), period=budget.period
                    )
                )
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
        """The pre-FRD-405 path: read the usage and refuse if a limit is already met.

        Used when no shared counter store is configured or it cannot be reached. It enforces,
        but concurrent requests remain invisible to each other — which is the whole reason the
        reservation path exists.
        """
        for budget in budgets:
            usage = await self._usage(
                session, _scope_key(budget, caller), _period_key(budget.period, now)
            )
            breached = None
            if budget.limit_cost_nanos is not None and usage.cost_nanos >= budget.limit_cost_nanos:
                breached = "cost"
            elif budget.limit_requests is not None and usage.requests >= budget.limit_requests:
                breached = "requests"
            elif budget.limit_tokens is not None and usage.tokens >= budget.limit_tokens:
                breached = "tokens"
            if breached:
                raise BudgetExceeded(
                    _BREACH_MESSAGES[breached].format(
                        scope=_scope_label(budget), period=budget.period
                    )
                )

    @asynccontextmanager
    async def hold(self, reservation: Reservation) -> AsyncIterator[Reservation]:
        """Guarantee that a reservation is resolved, whatever happens inside the block.

        Releasing at each failure site was one `except` clause short of correct: only
        ``UpstreamError`` was handled, so a malformed upstream body, a database hiccup in the
        pricing lookup or any outright bug left the reservation behind — and a budget that
        shrinks a little with every defect is one nobody can reason about. Making the guarantee
        structural means a future exit path cannot forget it.
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
        """Book the real figure: correct the reservation and persist it.

        Postgres receives the actual consumption; the shared counter is moved by the difference
        between what was reserved and what was really used, so it converges on the same total.

        ``requests`` is what the call weighed — one for an ordinary request, and one **per text**
        for an embedding batch (`FRD-113` FR-6). Settling a batch of 500 as a single request would
        hand back 499 of the reservation and leave a request-count budget unable to see batched
        traffic at all.
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

        Without this an upstream failure would consume budget permanently, so a provider outage
        would look to a use case exactly like having spent its month.
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
                    _scope_key(budget, reservation.subject),
                    period_key,
                    amounts=amounts,
                )
            except CountersUnavailable:
                # The counter keeps this request's estimate for now. It cannot be repaired from
                # here — the store holding the stale figure is the store that is unreachable —
                # but the damage is bounded: the counter expires well before its period does and
                # is rebuilt from Postgres, which has the settled figure (COUNTER_TTL_SECONDS).
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

        Both extra arguments are keyword-only on purpose: an amount of money and a timestamp
        next to each other as positionals is exactly how a caller ends up booking the wrong
        figure without anything failing.

        ``cost_nanos`` is ``None`` when the model has no price on file. Such a request is
        counted under ``unpriced_requests`` rather than as costing zero: a spend figure that
        silently omits traffic is worse than one that admits what it does not know.

        ``subject`` is who the request was from. It is required by a **per-person** budget, whose
        counter key *is* the caller; a shared row ignores it.
        """
        if not budgets:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            for budget in budgets:
                await session.execute(
                    _accumulate(
                        session,
                        scope_key=_scope_key(budget, subject),
                        period_key=_period_key(budget.period, now),
                        tokens=tokens,
                        requests=requests,
                        cost_nanos=cost_nanos,
                    )
                )
            await session.commit()

    async def refuse_if_exhausted(
        self,
        use_case: str | None,
        subject: str | None,
        now: datetime | None = None,
    ) -> None:
        """Refuse a use case that is **already** over a limit, before anything is spent on it.

        Not a reservation and not a substitute for `guard` — concurrent requests stay invisible to
        each other here, which is the whole reason the reservation exists. This runs earlier and
        answers a cheaper question: *has this use case already spent its allowance?* That question
        needs no model, so it can be asked before routing, before the pipeline, before anything
        costs money.

        It exists because it turned out the answer was being paid for. A use case one request over
        its cost limit kept running its LLM injection filter on every subsequent request — all of
        them refused with a 429, all of them billed for the classifier call. Measured: a 20 000
        limit, one served request, seven refused, and 72 400 spent. A client with a retry loop
        spends without bound.
        """
        if not use_case:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = await self._applicable(session, use_case, subject)
            if budgets:
                await self._check_only(session, budgets, now, subject)

    async def book_side_call(
        self,
        use_case: str | None,
        subject: str | None,
        tokens: int,
        *,
        cost_nanos: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Book tokens the **gateway** spent on the caller's behalf (`FRD-125`).

        A pipeline's classifier call is real money against the same use case, and it was counted as
        nothing at all — which `FRD-403` names as the one thing worse than an unpriced request.

        **Counted as a request** — the owner's decision (2026-08-15), reversing this method's
        original `requests=0`.

        The argument for zero was that the caller made one request and counting the classifier as
        a second inflates every request figure and can trip a *request* limit for traffic nobody
        sent. That consequence is now accepted deliberately, and the reason given for accepting it
        is the honest one: a step's call reaches a model and costs money, so a use case that runs
        two of them per request is doing three times the work its request budget was sized for, and
        a budget that cannot see that is a budget about something other than what is happening.

        **Rate limits are untouched**, and that is the same decision read the other way. A bucket
        measures how fast requests *arrive*; the gate is taken once, before the pipeline, on the
        one request that did arrive (`guard_before_work`). Refusing a caller because the gateway
        made calls on their behalf would slow down exactly the traffic they did send — which is
        what the original reasoning was protecting against, and it still applies there.

        Not a reservation. These tokens are already spent by the time their size is known — the
        pipeline runs before the reservation, because routing has to choose the model the
        reservation is made against. Booking after the fact is the honest description of that.

        **Both stores, not just Postgres.** The first version of this called ``record`` alone,
        which writes the system of record — so reporting was right and *enforcement was not*: the
        guard reads the shared counter (`FRD-405`/`ADR-0008`), and the classifier's spend reached
        it only when that counter expired and rebuilt from Postgres, up to `COUNTER_TTL_SECONDS`
        later. Found by setting a small cost cap and watching a use case sail past it: the counter
        said 41 000 against a limit of 40 000 and the next request was served.
        """
        if not use_case or tokens <= 0:
            return
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            budgets = await self._applicable(session, use_case, subject)
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
                    _scope_key(budget, subject),
                    _period_key(budget.period, now),
                    amounts=amounts,
                )
            except CountersUnavailable:
                # Postgres has it, and the counter rebuilds from Postgres when it expires. The
                # window is bounded and the direction is the safe one — the counter is *low*, so a
                # caller is under-charged rather than refused for spend that never happened.
                _log.warning("budget_side_call_adjust_degraded", budget_id=budget.id)

    async def usage(
        self,
        use_case: str,
        now: datetime | None = None,
        *,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Current-period usage per budget for a use case (for the UI consumption view, FRD-402).

        Cost is reported both as an exact decimal string and as raw nano-units: the string is
        what a human reads, the integer is what a progress bar can divide without a float
        creeping into a monetary figure.

        A **per-person** budget has no single figure — one configured row is N counters — so the
        answer depends on who is asking, and ``measured_for`` says whose figure this is. A reader
        the row does not bind (an oversight role, who is a member of nothing) gets ``None`` for
        every figure rather than a zero: `FRD-603`'s rule, that *unknown is never rendered as
        zero*, applies with more force here than anywhere, because zero is also what a real,
        untouched allowance looks like.
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
                # **Only `each_member` depends on who is reading.** A `member` row names its own
                # subject and is keyed on that; a `use_case` row names nobody. Passing the reader
                # as the caller for every row asked whether *they* are bound by a rule written
                # about somebody else — which for a named member row they are not, so
                # `Scope.applying` returned `None` and `_scope_key`'s assertion fired: a **500**
                # on the use-case page, for any reader looking at a use case that has a member
                # budget naming anyone but themselves.
                #
                # Introduced with the per-head scope (2026-08-11, `744337f`) and found the same
                # day by opening the console as `admin`. `None` restores the pre-existing meaning
                # — the row resolves to its own subject — while `each_member`, which genuinely
                # has one counter per reader, still resolves to the caller.
                caller = subject if budget.scope == EACH_MEMBER else None
                usage = await self._usage(
                    session,
                    _scope_key(budget, caller),
                    _period_key(budget.period, now),
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

    async def _applicable(
        self,
        session: AsyncSession,
        use_case: str,
        subject: str | None,
    ) -> list[BudgetRead]:
        result = await session.execute(
            select(BudgetRead).where(BudgetRead.use_case == use_case, BudgetRead.enabled.is_(True))
        )
        return [
            budget
            for budget in result.scalars()
            if Scope.applying(scope=budget.scope, use_case=budget.use_case, caller=subject)
            is not None
        ]

    async def _usage(self, session: AsyncSession, scope_key: str, period_key: str) -> _Usage:
        record = await session.get(BudgetUsage, (scope_key, period_key))
        if record is None:
            return _Usage(0, 0, 0, 0)
        return _Usage(record.tokens, record.requests, record.cost_nanos, record.unpriced_requests)


def _accumulate(
    session: AsyncSession,
    *,
    scope_key: str,
    period_key: str,
    tokens: int,
    requests: int,
    cost_nanos: int | None,
) -> Any:
    """One statement that inserts the counter or adds to it. **Found by concurrency, twice.**

    This was a read, then an insert-or-modify, then a commit, and both halves were racy:

    - Two requests arriving as the *first* of a period both found no row and both inserted one.
      One lost, with a ``UniqueViolation`` that reached the caller as a **500** for a request the
      gateway had already served and charged for. Twenty concurrent requests against a fresh
      budget produced two of them, every run.
    - Two requests updating an *existing* row each read the old value and wrote an absolute new
      one, so an increment was silently discarded. That failure has no error at all: the counter
      that is supposed to be the system of record drifts **below** the truth, in the direction
      that spends money, under exactly the load that makes a budget matter.

    So the arithmetic moves into the database, where the row is locked for the statement's
    duration. ``ON CONFLICT`` is spelled the same way by Postgres and SQLite and by nobody else,
    which is why the dialect is dispatched on rather than assumed.

    ``cost_nanos`` of ``None`` means the model had no price on file. It is counted under
    ``unpriced_requests`` rather than as zero: a spend figure that silently omits traffic is worse
    than one that admits what it does not know (`FRD-403`).
    """
    unpriced = requests if cost_nanos is None else 0
    values = {
        "scope_key": scope_key,
        "period_key": period_key,
        "tokens": tokens,
        "requests": requests,
        "cost_nanos": cost_nanos or 0,
        "unpriced_requests": unpriced,
    }
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(BudgetUsage).values(**values)

    columns = BudgetUsage.__table__.c
    return statement.on_conflict_do_update(
        index_elements=["scope_key", "period_key"],
        set_={
            "tokens": columns.tokens + tokens,
            "requests": columns.requests + requests,
            "cost_nanos": columns.cost_nanos + (cost_nanos or 0),
            "unpriced_requests": columns.unpriced_requests + unpriced,
        },
    )
