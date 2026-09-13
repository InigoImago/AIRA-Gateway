"""Measuring one rule against the audit trail (`FRD-501`).

Pure evaluation: given a session, a rule and a moment, say whether the rule is crossed and by how
much. Writing, scheduling and actions are :mod:`aira_gateway.anomalies.service` and `FRD-503`;
kept apart so the arithmetic over rows can be tested without a clock or a queue.
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

#: Who a `subject` rule is about: the username where recorded (`FRD-606`), else the subject —
#: :func:`aira_gateway.scopes.person` asked of a stored row, so one person's key and browser
#: traffic count together. `nullif` because SQL reads `""` as a value and `person` as absence.
_PERSON = func.coalesce(func.nullif(RequestLog.username, ""), RequestLog.subject)

#: The column each target groups by. A rule's target is what its action lands on, so it is also
#: what the measurement is *per* — a rate averaged over a use case hides the caller producing it.
_GROUP_BY = {
    RuleTarget.SUBJECT: _PERSON,
    RuleTarget.CREDENTIAL: RequestLog.credential,
    RuleTarget.USE_CASE: RequestLog.use_case,
}

#: Which outcomes each rate kind counts, from the closed `Outcome` vocabulary (`FRD-122`) rather
#: than a second list. ``None`` means anything not `served`, `client_gone` included: one hang-up is
#: not our failure, a thousand is what a detector exists to surface.
_COUNTED_OUTCOMES: dict[RuleKind, frozenset[str] | None] = {
    RuleKind.REFUSAL_RATE: None,
    RuleKind.ERROR_RATE: frozenset({Outcome.UPSTREAM_ERROR.value}),
    RuleKind.BLOCKED_PROMPT_RATE: frozenset({Outcome.BLOCKED_BY_PIPELINE.value}),
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One target that crossed a rule's threshold."""

    target_value: str
    observed: int
    sample: int
    detail: str


def _group_by(rule: AnomalyRuleRead) -> Any | None:
    """The column this rule measures per, or ``None`` if its target is not one this build has."""
    try:
        return _GROUP_BY[RuleTarget(rule.target)]
    except ValueError:
        return None


def _column(rule: AnomalyRuleRead) -> Any:
    """:func:`_group_by` for a rule `evaluate_rule` has admitted — the only way in."""
    group = _group_by(rule)
    assert group is not None, f"rule {rule.id} targets {rule.target!r}, which has no column"
    return group


def _scoped(stmt: Any, rule: AnomalyRuleRead, group: Any) -> Any:
    """Narrow a query to the rule's reach and to rows that have a target.

    A global rule (``use_case`` NULL) is not narrowed to a use case.
    """
    if rule.use_case is not None:
        stmt = stmt.where(RequestLog.use_case == rule.use_case)
    return stmt.where(group.is_not(None))


def _window(rule: AnomalyRuleRead, now: datetime) -> tuple[datetime, datetime]:
    """The window, and the window before it — the reference every ratio is measured against."""
    span = timedelta(minutes=rule.window_minutes)
    return now - span, now - 2 * span


async def evaluate_rule(
    session: AsyncSession, rule: AnomalyRuleRead, now: datetime | None = None
) -> list[Finding]:
    """Return every target of ``rule`` that crosses its threshold right now.

    A kind or target this build does not implement (a newer Management; `consumer.apply` writes both
    verbatim) measures nothing and **says so** in the log: passing would be a statement about the
    traffic, and raising would abort the whole tick — `service.tick` has no per-rule boundary — and
    stop every other rule for good.
    """
    moment = now or datetime.now(UTC)
    try:
        kind = RuleKind(rule.kind)
    except ValueError:
        _log.warning("anomaly_rule_kind_not_implemented", rule_id=rule.id, kind=rule.kind)
        return []
    if _group_by(rule) is None:
        _log.warning("anomaly_rule_target_not_implemented", rule_id=rule.id, target=rule.target)
        return []
    if kind in _COUNTED_OUTCOMES:
        return await _evaluate_rate(session, rule, kind, moment)
    if kind is RuleKind.PAYLOAD_SIZE:
        return await _evaluate_payload_size(session, rule, moment)
    if kind in RATIO_KINDS:
        return await _evaluate_ratio(session, rule, kind, moment)
    if kind is RuleKind.NEW_SOURCE_IP:
        return await _evaluate_new_source(session, rule, moment)
    # `FRD-500`'s vocabulary is closed so this stays unreachable; a rule reaching it is announced.
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

    Rows with no byte count (older than `FRD-501`) are excluded from **both** sides: counting an
    unknown as small would make an old use case look innocent.
    """
    if not rule.parameter:
        # Management refuses such a rule; an older event could still carry one.
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
    group = _column(rule)
    hits = func.sum(func.cast(matches, Integer)).label("hits")
    stmt = select(group, func.count().label("total"), hits).where(RequestLog.created_at >= since)
    if known_only is not None:
        stmt = stmt.where(known_only)
    stmt = _scoped(stmt, rule, group).group_by(group)

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
    group = _column(rule)
    measure = (
        func.coalesce(func.sum(RequestLog.cost_nanos), 0)
        if kind is RuleKind.SPEND_SPIKE
        else func.count()
    )

    async def totals(start: datetime, end: datetime) -> dict[str, tuple[int, int]]:
        stmt = select(group, measure, func.count()).where(
            RequestLog.created_at >= start, RequestLog.created_at < end
        )
        stmt = _scoped(stmt, rule, group).group_by(group)
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
        # Growth from nothing is not a multiple of anything: treating it as infinite would make
        # every use case's first hour an incident.
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
    group = _column(rule)

    async def seen(start: datetime, end: datetime) -> dict[str, set[str]]:
        stmt = select(group, RequestLog.source_ip).where(
            RequestLog.created_at >= start,
            RequestLog.created_at < end,
            RequestLog.source_ip.is_not(None),
        )
        stmt = _scoped(stmt, rule, group).distinct()
        found: dict[str, set[str]] = {}
        for value, ip in (await session.execute(stmt)).all():
            found.setdefault(str(value), set()).add(str(ip))
        return found

    current = await seen(since, now + timedelta(seconds=1))
    known = await seen(before, since)

    findings: list[Finding] = []
    for value, addresses in current.items():
        # No reference yet: right after deployment every address is new, and reporting that would
        # be reporting the deployment. The reference is one window long, so this clears itself.
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
