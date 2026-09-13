"""What the anomaly detector has found (`FRD-501` FR-8), scoped like every other figure."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from aira_common.anomalies import RuleTarget
from aira_gateway.api.reporting.common import parse_cursor, router, scope_label, visible_scope
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import AnomalyEvent
from aira_gateway.payloads import restricted_use_cases
from aira_gateway.state import sessionmaker_of

#: Findings per page by default, and the most one page may hold.
ANOMALY_PAGE = 50
MAX_ANOMALY_PAGE = 200


def _about_this_caller(restricted: list[str], principal: Principal) -> ColumnElement[bool]:
    """Findings a caller may see in a use case that shows members only their own traffic.

    Written over `AnomalyEvent`, never borrowed from the trace view's `RequestLog` condition: a
    foreign column joins its table with no predicate — a cartesian product that fails open
    (`test_scoped_reads_stay_on_their_table.py`). What is withheld is other people's activity:

    - ``target = use_case`` names nobody, and is kept.
    - ``target = subject`` is kept where it is this caller.
    - ``target = credential`` is kept where it is this caller's own key prefix.
    """
    own: list[ColumnElement[bool]] = [AnomalyEvent.target == RuleTarget.USE_CASE.value]
    # Either alphabet: `target_value` is grouped from `RequestLog.subject`, a directory id for an
    # OIDC caller and a username for an API key — the widening `payloads.own_requests` makes.
    names = {name for name in (principal.subject, principal.person) if name}
    if names:
        own.append(
            and_(
                AnomalyEvent.target == RuleTarget.SUBJECT.value,
                AnomalyEvent.target_value.in_(sorted(names)),
            )
        )
    if principal.credential:
        own.append(
            and_(
                AnomalyEvent.target == RuleTarget.CREDENTIAL.value,
                AnomalyEvent.target_value == principal.credential,
            )
        )
    return or_(AnomalyEvent.use_case.notin_(restricted), or_(*own))


@router.get("/v1beta/anomalies")
async def anomalies(
    request: Request,
    principal: Principal = Depends(require_principal),
    limit: int = Query(ANOMALY_PAGE, ge=1, le=MAX_ANOMALY_PAGE),
    cursor: str = Query("", max_length=128),
    use_case: str = Query("", max_length=64),
) -> JSONResponse:
    """What the detector has found (`FRD-501` FR-8), newest first.

    A global rule's findings are shown to whoever may see the use case the traffic belonged to; a
    finding with no use case is oversight-only, since there is nobody else it is about.
    """
    scope = visible_scope(principal)
    if use_case and scope is not None and use_case not in scope:
        # Emptiness rather than a refusal, with `in_scope` saying which empty this is.
        return JSONResponse(
            {"events": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}
        )

    stmt = select(AnomalyEvent)
    if scope is not None:
        stmt = stmt.where(AnomalyEvent.use_case.in_(list(scope)))
    # In the query, not the browser: a busy use case's findings would push a quiet one's off the
    # page.
    if use_case:
        stmt = stmt.where(AnomalyEvent.use_case == use_case)

    # A use case may show its users only their own requests (`FRD-505` FR-4) — applied to the list,
    # since the rows alone disclose who else calls and how often.
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        restricted = await restricted_use_cases(session, principal)
    if restricted:
        stmt = stmt.where(_about_this_caller(restricted, principal))

    # By `(created_at, id)` cursor, not offset: findings are append-only, and an offset page shows
    # one row twice and skips another while the detector fires. Written out because SQLite has no
    # tuple comparison.
    if cursor:
        at, row_id = parse_cursor(cursor)
        stmt = stmt.where(
            or_(
                AnomalyEvent.created_at < at,
                and_(AnomalyEvent.created_at == at, AnomalyEvent.id < row_id),
            )
        )
    stmt = stmt.order_by(AnomalyEvent.created_at.desc(), AnomalyEvent.id.desc()).limit(limit + 1)

    async with sessionmaker() as session:
        rows = list((await session.execute(stmt)).scalars().all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = f"{rows[-1].created_at.isoformat()}|{rows[-1].id}" if has_more and rows else None
    return JSONResponse(
        {
            "events": [_event(row) for row in rows],
            "next_cursor": next_cursor,
            "scope": scope_label(scope),
            "in_scope": True,
        }
    )


def _event(row: AnomalyEvent) -> dict[str, Any]:
    """One finding on the wire."""
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "rule": row.rule_name,
        "kind": row.kind,
        "use_case": row.use_case,
        "target": row.target,
        "target_value": row.target_value,
        "observed": row.observed,
        "threshold": row.threshold,
        "sample": row.sample,
        "window_minutes": row.window_minutes,
        # What was *done*, kept apart from what was detected (`ADR-0014` §3).
        "action_taken": row.action_taken,
        "detail": row.detail,
    }
