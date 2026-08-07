"""Scheduling the evaluation, and recording what it found (`FRD-501`).

Off the request path entirely (`ADR-0014`): a background task on an interval, evaluating only the
scopes that saw traffic since the last tick. A request pays nothing — not a query, not a lock, not
a counter.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.anomalies import RuleAction, RuleTarget
from aira_gateway.anomalies.evaluator import Finding, evaluate_rule
from aira_gateway.anomalies.suspensions import SuspensionService, suspension_from_rule
from aira_gateway.db.models import AnomalyEvent, AnomalyRuleRead

_log = structlog.get_logger(__name__)

#: How many touched scopes to remember between ticks. A bounded loss delays a finding by one tick;
#: an unbounded set is a memory leak in the component whose job is to still be running when
#: something goes wrong.
MAX_TOUCHED = 4096

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
    #: Use-case slugs seen since the last tick. ``None`` marks traffic with no use case, which a
    #: global rule still measures.
    _touched: set[str | None] = field(default_factory=set)
    _last_fired: dict[tuple[int, str], datetime] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None

    # -- the hot-path side, which must stay this cheap -------------------------------------

    def touch(self, use_case: str | None) -> None:
        """Note that this scope saw traffic. Called from the audit writer, never from a route."""
        if len(self._touched) >= MAX_TOUCHED:
            return
        self._touched.add(use_case)

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
        """Evaluate every rule that could have been affected since the last tick."""
        touched, self._touched = self._touched, set()
        if not touched:
            # Nothing happened. A quiet installation with 200 use cases should not run 200 queries
            # a minute forever.
            return []

        moment = now or datetime.now(UTC)
        scoped = {slug for slug in touched if slug is not None}
        async with self.sessionmaker() as session:
            rules = await self._applicable(session, scoped)
            written: list[AnomalyEvent] = []
            for rule in rules:
                for finding in await evaluate_rule(session, rule, moment):
                    event = self._record(session, rule, finding, moment)
                    if event is not None:
                        written.append(event)
            if written:
                await session.commit()
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

    def _record(
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
        key = (rule.id, finding.target_value)
        last = self._last_fired.get(key)
        if last is not None and now - last < timedelta(minutes=rule.window_minutes):
            return None
        self._last_fired[key] = now

        action = RuleAction(rule.action)
        taken = self._enforce(session, rule, finding, action, now)
        event = AnomalyEvent(
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
