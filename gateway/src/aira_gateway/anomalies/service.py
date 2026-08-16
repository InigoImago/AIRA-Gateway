"""Scheduling the evaluation, and recording what it found (`FRD-501`).

Off the request path entirely (`ADR-0014`): a background task on an interval, evaluating only the
scopes that saw traffic since the last tick. A request pays nothing — not a query, not a lock, not
a counter.

**Correct with more than one gateway instance** (`FRD-127`), which it was not. Every instance runs
this loop, and three pieces of it used to be per-process: the touched set, the cooldown map, and
the decision to evaluate at all. Two instances therefore evaluated the same shared `request_logs`,
reached the same verdict, and wrote an event each — while both sat inside their own cooldowns, so
the mechanism meant to stop repeat firing was the one thing that could not see the repeat. With
enforcement on, one finding became one suspension per instance.

The three answers are all in this module and each is a *shared* fact rather than a local one:

- **which scopes saw traffic** is read from `request_logs`, so the instance that evaluates sees the
  whole fleet's traffic rather than the requests it happened to serve;
- **the cooldown** is the `anomaly_events` table, so it holds across instances *and* across
  restarts — a rolling update used to re-fire every rule as each new instance started with an empty
  map;
- **one evaluator per tick**, claimed with a Postgres advisory lock held for the transaction.

Kept in the serving process rather than moved to a worker of its own, which was the first plan
(`FRD-127` §5.3 option 1). Moving it would have made the singleton structural, and it would also
have meant that every existing deployment silently stopped detecting anything until its operator
added a container. A capability that disappears on upgrade unless somebody reads the release notes
is worse than one that needs a lock.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.anomalies import RuleAction, RuleTarget
from aira_gateway.anomalies.evaluator import Finding, evaluate_rule
from aira_gateway.anomalies.suspensions import SuspensionService, suspension_from_rule
from aira_gateway.db.models import AnomalyEvent, AnomalyRuleRead, RequestLog

_log = structlog.get_logger(__name__)

#: The advisory-lock key the evaluator claims each tick. An arbitrary constant; what matters is
#: that every gateway instance uses the same one, so `pg_try_advisory_xact_lock` is a fleet-wide
#: "am I the evaluator this minute".
#:
#: The **transaction-scoped** form deliberately: it is released when the transaction ends, however
#: it ends. A session-scoped lock survives a crashed process until its connection is reaped, which
#: would leave the fleet with no evaluator at all — turning a duplicate-detection defect into an
#: absent-detection one, which is far worse.
TICK_LOCK_KEY = 0x4149_5241_414E_4F4D

#: What an event records when a rule asked for an action that could not be carried out. `FRD-503`
#: made the ordinary case possible; this remains for the one that is not — a `block` rule whose
#: `action_minutes` never arrived, say. Said in the row rather than left to inference: a control
#: displayed as active and doing nothing is the defect `FRD-125` exists to prevent.
NOT_ENFORCED = "detected_not_enforced"


@dataclass
class AnomalyService:
    """Evaluates the rules on a timer, over the scopes that saw traffic."""

    sessionmaker: async_sessionmaker[AsyncSession]
    interval_seconds: float = 60.0
    enabled: bool = True
    #: Where a fired rule's decision goes. Optional so the evaluator can be exercised without one,
    #: and so an installation with enforcement switched off still detects and records.
    suspensions: SuspensionService | None = None
    #: How far this instance has evaluated. Rows at or after it are still unexamined.
    #:
    #: Replaces a set the audit writer filled in-process. That set held only the requests *this*
    #: instance served, so with a load balancer in front the instance that evaluated knew about a
    #: fraction of the traffic and the rest was never measured by any rule.
    _since: datetime | None = None
    _task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                # A detector that dies on one bad tick is a detector that is off when it matters.
                _log.warning("anomaly_tick_failed", error=str(exc))

    # -- one round -------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[AnomalyEvent]:
        """Evaluate every rule that could have been affected since the last tick.

        **The watermark only moves on success.** A tick that raised — a database blink, a rule the
        evaluator could not read — used to take its window with it: the scopes were cleared before
        the evaluation ran, so the traffic in those minutes was never measured by any rule.
        Detection is asynchronous by `ADR-0014`, which promises it happens *later*, not that it may
        quietly not happen. Leaving `_since` where it is re-reads the same window next time, and
        needs no merging back of a set that has been growing in the meantime.
        """
        moment = now or datetime.now(UTC)
        async with self.sessionmaker() as session:
            if not await self._claim_the_tick(session):
                # Another instance is evaluating this minute. Its query covers the fleet's traffic
                # rather than its own, so this instance's window has been handled — advance and
                # stop. Not advancing would leave a loser re-reading hours of logs on the day it
                # finally wins one.
                self._since = moment
                return []
            touched = await self._touched_since(session, moment)
            written = await self._evaluate(session, touched, moment)
            if written:
                await session.commit()
        self._since = moment
        return written

    async def _claim_the_tick(self, session: AsyncSession) -> bool:
        """Whether this instance is the one evaluating, fleet-wide (`FRD-127`).

        The **transaction-scoped** advisory lock: released when this transaction ends, however it
        ends. A session-scoped one survives a crashed process until its connection is reaped, which
        would leave the fleet with no evaluator — turning a duplicate-detection defect into an
        absent-detection one, which is much worse.

        SQLite has no advisory locks and no second instance either, so it always wins. The hermetic
        tests exercise the evaluation; two real evaluators over one Postgres belong to the
        integration layer, which is where that property is actually observable.
        """
        if session.get_bind().dialect.name != "postgresql":
            return True
        claimed = await session.execute(select(func.pg_try_advisory_xact_lock(TICK_LOCK_KEY)))
        return bool(claimed.scalar())

    async def _touched_since(self, session: AsyncSession, moment: datetime) -> set[str | None]:
        """Which scopes saw traffic, read from the audit rows rather than from this process.

        ``None`` is a real member of this set rather than an absence: it marks traffic with no use
        case, which a global rule still measures.

        The first tick after a start looks back one interval. Anything older was either evaluated
        by the instance this one replaced, or is outside every rule's window anyway.
        """
        since = self._since or (moment - timedelta(seconds=self.interval_seconds))
        stmt = select(RequestLog.use_case).where(RequestLog.created_at >= since).distinct()
        return set((await session.execute(stmt)).scalars().all())

    async def _evaluate(
        self, session: AsyncSession, touched: set[str | None], moment: datetime
    ) -> list[AnomalyEvent]:
        if not touched:
            # Nothing happened. A quiet installation with 200 use cases should not run 200 queries
            # a minute forever.
            return []

        scoped = {slug for slug in touched if slug is not None}
        rules = await self._applicable(session, scoped)
        written: list[AnomalyEvent] = []
        for rule in rules:
            for finding in await evaluate_rule(session, rule, moment):
                event = await self._record(session, rule, finding, moment)
                if event is not None:
                    written.append(event)
        return written

    async def _applicable(self, session: AsyncSession, touched: set[str]) -> list[AnomalyRuleRead]:
        """The enabled rules that could have been affected: global ones, and those of a use case
        that saw traffic."""
        stmt = select(AnomalyRuleRead).where(AnomalyRuleRead.enabled.is_(True))
        rules = list((await session.execute(stmt)).scalars().all())
        return [r for r in rules if r.use_case is None or r.use_case in touched]

    def _enforce(
        self,
        session: AsyncSession,
        rule: AnomalyRuleRead,
        finding: Finding,
        action: RuleAction,
        now: datetime,
    ) -> str:
        """Carry out the rule's action, and return what was **actually** done.

        Recording and enforcing are two facts (`ADR-0014` §3), so the row says which happened
        rather than repeating what the rule asked for. That is also what makes the alert-first
        rollout real: "detected, action alert" is a first-class outcome, not a failure to act.
        """
        if action is RuleAction.ALERT:
            return RuleAction.ALERT.value
        if self.suspensions is None or not rule.action_minutes:
            # A rule that cannot be carried out says so on the row rather than looking enforced.
            return NOT_ENFORCED
        session.add(
            suspension_from_rule(
                rule_name=rule.name,
                use_case=rule.use_case,
                target=rule.target,
                target_value=finding.target_value,
                action=action.value,
                minutes=rule.action_minutes,
                throttle_rpm=rule.throttle_rpm,
                detail=finding.detail,
                now=now,
            )
        )
        # The writer sees its own decision on the next request rather than after the TTL.
        self.suspensions.invalidate()
        return "blocked" if action is RuleAction.BLOCK else "throttled"

    async def _fired_recently(
        self, session: AsyncSession, rule: AnomalyRuleRead, target_value: str, now: datetime
    ) -> bool:
        """Has this exact finding already been recorded inside the rule's own window?

        **Asked of the table, not of a dict.** The cooldown used to be per process, which made it
        useless for the two things it most needed to survive: a second instance (each sat inside
        its own cooldown while the fleet fired once per instance) and a **restart** — a rolling
        update brought up an instance with an empty map, so every rule fired again the moment it
        started, describing traffic the previous instance had already reported.

        Autoflush makes an event added earlier in this same tick visible here, so a rule that finds
        the same target twice in one round still writes once.
        """
        cutoff = now - timedelta(minutes=rule.window_minutes)
        stmt = (
            select(AnomalyEvent.id)
            .where(
                AnomalyEvent.rule_id == rule.id,
                AnomalyEvent.target_value == target_value,
                AnomalyEvent.created_at > cutoff,
            )
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None

    async def _record(
        self,
        session: AsyncSession,
        rule: AnomalyRuleRead,
        finding: Finding,
        now: datetime,
    ) -> AnomalyEvent | None:
        """Write the finding, unless the same one was written within the rule's own window.

        The cooldown is the window itself: a 15-minute window evaluated every minute would
        otherwise fire fifteen times about the same fifteen minutes, and each event would describe
        traffic the previous one already described.
        """
        if await self._fired_recently(session, rule, finding.target_value, now):
            return None

        action = RuleAction(rule.action)
        taken = self._enforce(session, rule, finding, action, now)
        event = AnomalyEvent(
            # Stamped with the moment this evaluation is *about*, rather than left to the column's
            # server default. `_fired_recently` compares against it, so the two must be the same
            # clock — in production they are (`moment` is `now(UTC)`), and everywhere else this is
            # what makes the window testable at all.
            created_at=now,
            rule_id=rule.id,
            rule_name=rule.name,
            kind=rule.kind,
            use_case=rule.use_case
            if rule.use_case is not None
            else (finding.target_value if RuleTarget(rule.target) is RuleTarget.USE_CASE else None),
            target=rule.target,
            target_value=finding.target_value,
            observed=finding.observed,
            threshold=rule.threshold,
            sample=finding.sample,
            window_minutes=rule.window_minutes,
            action_taken=taken,
            detail=finding.detail[:500],
        )
        session.add(event)
        _log.info(
            "anomaly_detected",
            rule=rule.name,
            kind=rule.kind,
            target=rule.target,
            target_value=finding.target_value,
            observed=finding.observed,
            threshold=rule.threshold,
        )
        return event
