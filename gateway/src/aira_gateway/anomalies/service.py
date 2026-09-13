"""Scheduling the evaluation, and recording what it found (`FRD-501`).

Off the request path (`ADR-0014`): a background task on an interval, evaluating only the scopes
that saw traffic since the last tick. A request pays nothing.

Correct with several gateway instances (`FRD-127`) because each piece of state is shared rather
than per-process:

- **which scopes saw traffic** is read from `request_logs`, so the evaluator sees the fleet's
  traffic, not only what it served;
- **the cooldown** is the `anomaly_events` table, so it holds across instances and restarts;
- **one evaluator per tick**, claimed with a transaction-scoped Postgres advisory lock.

Kept in the serving process rather than a worker of its own (`FRD-127` §5.3), so an existing
deployment keeps detecting after an upgrade without adding a container.
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

#: The advisory-lock key every instance claims each tick — a fleet-wide "am I the evaluator this
#: minute". Taken **transaction-scoped**, so it is released however the transaction ends: a
#: session-scoped lock would survive a crashed process until its connection is reaped and leave the
#: fleet with no evaluator at all.
TICK_LOCK_KEY = 0x4149_5241_414E_4F4D

#: What an event records when a rule's action could not be carried out (a `block` rule without
#: `action_minutes`, say) — said on the row rather than looking enforced (`FRD-125`).
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
    #: How far this instance has evaluated; rows at or after it are still unexamined.
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
                # A detector that dies on one bad tick is off when it matters.
                _log.warning("anomaly_tick_failed", error=str(exc))

    # -- one round -------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[AnomalyEvent]:
        """Evaluate every rule that could have been affected since the last tick.

        **The watermark only moves on success**: a tick that raised re-reads the same window next
        time. `ADR-0014` promises detection happens later, not that it may quietly not happen.
        """
        moment = now or datetime.now(UTC)
        async with self.sessionmaker() as session:
            if not await self._claim_the_tick(session):
                # Another instance is evaluating, over the fleet's traffic — advance and stop, or a
                # loser would re-read hours of logs on the day it finally wins.
                self._since = moment
                return []
            touched = await self._touched_since(session, moment)
            written = await self._evaluate(session, touched, moment)
            if written:
                await session.commit()
        self._since = moment
        return written

    async def _claim_the_tick(self, session: AsyncSession) -> bool:
        """Whether this instance is the one evaluating, fleet-wide (`FRD-127`; see `TICK_LOCK_KEY`).

        SQLite has no advisory locks and no second instance, so it always wins; two real
        evaluators over one Postgres belong to the integration layer.
        """
        if session.get_bind().dialect.name != "postgresql":
            return True
        claimed = await session.execute(select(func.pg_try_advisory_xact_lock(TICK_LOCK_KEY)))
        return bool(claimed.scalar())

    async def _touched_since(self, session: AsyncSession, moment: datetime) -> set[str | None]:
        """Which scopes saw traffic, read from the audit rows rather than from this process.

        ``None`` is a real member: traffic with no use case, which a global rule still measures.
        The first tick after a start looks back one interval.
        """
        since = self._since or (moment - timedelta(seconds=self.interval_seconds))
        stmt = select(RequestLog.use_case).where(RequestLog.created_at >= since).distinct()
        return set((await session.execute(stmt)).scalars().all())

    async def _evaluate(
        self, session: AsyncSession, touched: set[str | None], moment: datetime
    ) -> list[AnomalyEvent]:
        if not touched:
            # A quiet installation should not run a query per rule every minute forever.
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

        Recording and enforcing are two facts (`ADR-0014` §3), so the row says which happened;
        "detected, action alert" is a first-class outcome, not a failure to act.
        """
        if action is RuleAction.ALERT:
            return RuleAction.ALERT.value
        if self.suspensions is None or not rule.action_minutes:
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

        Asked of the table, so the cooldown holds across instances and restarts. Autoflush makes an
        event added earlier in this tick visible, so one round writes a finding once.
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

        The cooldown is the window itself: otherwise a 15-minute window evaluated every minute
        would fire fifteen times about the same fifteen minutes.
        """
        if await self._fired_recently(session, rule, finding.target_value, now):
            return None

        action: RuleAction | None
        try:
            action = RuleAction(rule.action)
        except ValueError:
            # An action word this build does not implement (`consumer.apply` writes it verbatim).
            # The finding is still written, as `detected_not_enforced` rather than an action nobody
            # configured; raising would abort the whole tick.
            _log.warning("anomaly_rule_action_not_implemented", rule_id=rule.id, action=rule.action)
            action = None
        taken = (
            NOT_ENFORCED if action is None else self._enforce(session, rule, finding, action, now)
        )
        event = AnomalyEvent(
            # The moment this evaluation is about, not the server default: `_fired_recently`
            # compares against it, so both must read the same clock.
            created_at=now,
            rule_id=rule.id,
            rule_name=rule.name,
            kind=rule.kind,
            use_case=rule.use_case
            if rule.use_case is not None
            # The stored word, not coerced through `RuleTarget`, which would raise on a target this
            # build does not know.
            else (finding.target_value if rule.target == RuleTarget.USE_CASE.value else None),
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
