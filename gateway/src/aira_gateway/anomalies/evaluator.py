"""Measuring one rule against the audit trail (`FRD-501`).

Pure evaluation: given a session, a rule and a moment, say whether the rule is crossed and by how
much. No writing, no scheduling, no actions — those are :mod:`aira_gateway.anomalies.service` and
`FRD-503`. Kept separate because every interesting question here is arithmetic over rows, and
arithmetic is worth being able to test without a clock or a queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.anomalies import RATIO_KINDS, RuleKind, RuleTarget
from aira_common.logging import get_logger
from aira_gateway.audit import Outcome
from aira_gateway.db.models import AnomalyRuleRead, RequestLog

_log = get_logger("aira_gateway.anomalies")


@dataclass(frozen=True, slots=True)
class Finding:
    """One target that crossed a rule's threshold."""

    target_value: str
    observed: int
    sample: int
    detail: str


#: The column each target groups by. A rule's target is what its action lands on, so it is also
#: what the measurement has to be *per* — a refusal rate averaged over a whole use case says
#: nothing about the one caller producing it.
_GROUP_BY = {
    RuleTarget.SUBJECT: RequestLog.subject,
    RuleTarget.CREDENTIAL: RequestLog.credential,
    RuleTarget.USE_CASE: RequestLog.use_case,
}

#: Which outcomes each rate kind counts. Taken from the closed vocabulary in
#: :class:`aira_gateway.audit.Outcome` rather than restated: `FRD-122` made that enum the one place
#: a control's existence is recorded, and a second list here would go stale the first time somebody
#: added a control.
#:
#: ``None`` means "anything that is not `served`" — `client_gone` included, deliberately. One caller
#: hanging up is not our failure; a thousand is exactly the shape a detector exists to surface.
_COUNTED_OUTCOMES: dict[RuleKind, frozenset[str] | None] = {
    RuleKind.REFUSAL_RATE: None,
    RuleKind.ERROR_RATE: frozenset({Outcome.UPSTREAM_ERROR.value}),
    RuleKind.BLOCKED_PROMPT_RATE: frozenset({Outcome.BLOCKED_BY_PIPELINE.value}),
}


def _scoped(stmt: Any, rule: AnomalyRuleRead) -> Any:
    """Narrow a query to the rule's reach. A global rule (``use_case`` NULL) narrows to nothing."""
    if rule.use_case is not None:
        stmt = stmt.where(RequestLog.use_case == rule.use_case)
    return stmt


def _window(rule: AnomalyRuleRead, now: datetime) -> tuple[datetime, datetime]:
    """The window, and the window before it — the reference every ratio is measured against."""
    span = timedelta(minutes=rule.window_minutes)
    return now - span, now - 2 * span


async def evaluate_rule(
    session: AsyncSession, rule: AnomalyRuleRead, now: datetime | None = None
) -> list[Finding]:
    """Return every target of ``rule`` that crosses its threshold right now."""
    moment = now or datetime.now(UTC)
    try:
        kind = RuleKind(rule.kind)
    except ValueError:
        # A newer Management can publish a kind this gateway does not implement. Measuring nothing
        # is the forward-compatible answer, and the *only* safe one: passing would report a rule as
        # having found nothing, which is a statement about traffic rather than about this version.
        #
        # **Said out loud**, which is the rule `consumer.apply` reached on 2026-08-18 about exactly
        # this shape: tolerance does not require silence. A rule that measures nothing is a control
        # an operator believes is watching and is not, and the only symptom is the console showing
        # it as enabled — one log line makes that a search instead of an investigation.
        _log.warning("anomaly_rule_kind_not_implemented", rule_id=rule.id, kind=rule.kind)
        return []
    if kind in _COUNTED_OUTCOMES:
        return await _evaluate_rate(session, rule, kind, moment)
    if kind is RuleKind.PAYLOAD_SIZE:
        return await _evaluate_payload_size(session, rule, moment)
    if kind in RATIO_KINDS:
        return await _evaluate_ratio(session, rule, kind, moment)
    if kind is RuleKind.NEW_SOURCE_IP:
        return await _evaluate_new_source(session, rule, moment)
    # A kind the engine does not implement measures nothing, and says so by measuring nothing —
    # never by passing. `FRD-500`'s vocabulary is closed precisely so this branch stays empty, and
    # a rule that reaches it is announced for the same reason as the one above.
    _log.warning("anomaly_rule_kind_not_measured", rule_id=rule.id, kind=rule.kind)
    return []


async def _evaluate_rate(
    session: AsyncSession, rule: AnomalyRuleRead, kind: RuleKind, now: datetime
) -> list[Finding]:
    counted = _COUNTED_OUTCOMES[kind]
    if counted is None:
        matches = RequestLog.outcome != Outcome.SERVED.value
    else:
        matches = RequestLog.outcome.in_(list(counted))
    return await _share(
        session,
        rule,
        now,
        matches=matches,
        noun="requests",
        described=f"{kind.value.replace('_', ' ')}",
    )


async def _evaluate_payload_size(
    session: AsyncSession, rule: AnomalyRuleRead, now: datetime
) -> list[Finding]:
    """Share of requests at least ``parameter`` bytes.

    Rows with no byte count are excluded from **both** sides: they predate `FRD-501` and their size
    is unknown, and counting an unknown as small would make an old use case look innocent.
    """
    if not rule.parameter:
        # A `payload_size` rule with no byte figure measures nothing. Management refuses to create
        # one; an older event could still carry one, and measuring nothing beats guessing.
        return []
    return await _share(
        session,
        rule,
        now,
        matches=RequestLog.request_bytes >= rule.parameter,
        noun="requests",
        described=f"requests of at least {rule.parameter} bytes",
        known_only=RequestLog.request_bytes.is_not(None),
    )


async def _share(
    session: AsyncSession,
    rule: AnomalyRuleRead,
    now: datetime,
    *,
    matches: Any,
    noun: str,
    described: str,
    known_only: Any = None,
) -> list[Finding]:
    """Percentage of rows in the window matching ``matches``, per target."""
    since, _ = _window(rule, now)
    group = _GROUP_BY[RuleTarget(rule.target)]
    hits = func.sum(func.cast(matches, Integer)).label("hits")
    stmt = select(group, func.count().label("total"), hits).where(RequestLog.created_at >= since)
    if known_only is not None:
        stmt = stmt.where(known_only)
    stmt = _scoped(stmt, rule).where(group.is_not(None)).group_by(group)

    findings: list[Finding] = []
    for value, total, matched in (await session.execute(stmt)).all():
        # A rate over too few rows says nothing: one refusal out of one request is 100 %.
        if total < max(rule.min_sample, 1):
            continue
        share = round((matched or 0) * 100 / total)
        if share < rule.threshold:
            continue
        findings.append(
            Finding(
                target_value=str(value),
                observed=share,
                sample=total,
                detail=f"{share}% {described} over {total} {noun} in {rule.window_minutes} min",
            )
        )
    return findings


async def _evaluate_ratio(
    session: AsyncSession, rule: AnomalyRuleRead, kind: RuleKind, now: datetime
) -> list[Finding]:
    """This window as a percentage of the one before it."""
    since, before = _window(rule, now)
    group = _GROUP_BY[RuleTarget(rule.target)]
    measure = (
        func.coalesce(func.sum(RequestLog.cost_nanos), 0)
        if kind is RuleKind.SPEND_SPIKE
        else func.count()
    )

    async def totals(start: datetime, end: datetime) -> dict[str, tuple[int, int]]:
        stmt = select(group, measure, func.count()).where(
            RequestLog.created_at >= start, RequestLog.created_at < end
        )
        stmt = _scoped(stmt, rule).where(group.is_not(None)).group_by(group)
        return {
            str(value): (int(amount or 0), int(rows))
            for value, amount, rows in (await session.execute(stmt)).all()
        }

    current = await totals(since, now + timedelta(seconds=1))
    previous = await totals(before, since)

    findings: list[Finding] = []
    for value, (amount, rows) in current.items():
        if rows < max(rule.min_sample, 1):
            continue
        was, _ = previous.get(value, (0, 0))
        # Growth from nothing is not a multiple of anything. Treating it as infinite would make
        # every use case's first hour an incident, and the alert that fires on arrival is the one
        # people switch off before it ever says anything true.
        if was <= 0:
            continue
        share = round(amount * 100 / was)
        if share < rule.threshold:
            continue
        unit = "spend" if kind is RuleKind.SPEND_SPIKE else "requests"
        findings.append(
            Finding(
                target_value=value,
                observed=share,
                sample=rows,
                detail=(
                    f"{unit} at {share}% of the previous {rule.window_minutes} min "
                    f"({amount} against {was})"
                ),
            )
        )
    return findings


async def _evaluate_new_source(
    session: AsyncSession, rule: AnomalyRuleRead, now: datetime
) -> list[Finding]:
    """Addresses seen in the window that were not seen in the window before it."""
    since, before = _window(rule, now)
    group = _GROUP_BY[RuleTarget(rule.target)]

    async def seen(start: datetime, end: datetime) -> dict[str, set[str]]:
        stmt = select(group, RequestLog.source_ip).where(
            RequestLog.created_at >= start,
            RequestLog.created_at < end,
            RequestLog.source_ip.is_not(None),
        )
        stmt = _scoped(stmt, rule).where(group.is_not(None)).distinct()
        found: dict[str, set[str]] = {}
        for value, ip in (await session.execute(stmt)).all():
            found.setdefault(str(value), set()).add(str(ip))
        return found

    current = await seen(since, now + timedelta(seconds=1))
    known = await seen(before, since)

    findings: list[Finding] = []
    for value, addresses in current.items():
        # Nothing to compare against: on the first evaluation after deployment every address is
        # new, and reporting that would be reporting the deployment. The reference is one window
        # long, so the warm-up clears itself.
        if value not in known:
            continue
        fresh = sorted(addresses - known[value])
        if len(fresh) < rule.threshold:
            continue
        findings.append(
            Finding(
                target_value=value,
                observed=len(fresh),
                sample=len(addresses),
                detail=f"used from {len(fresh)} address(es) not seen before: {', '.join(fresh)}",
            )
        )
    return findings
