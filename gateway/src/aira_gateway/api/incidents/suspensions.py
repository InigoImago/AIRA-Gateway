"""The kill switch: stop traffic by hand, list what was stopped, restore it (`FRD-503`).

Deliberately **not** routed through Management and Kafka like other configuration: an incident
control that depends on the event bus fails exactly when the bus is the problem (§4.3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from aira_common.anomalies import RuleAction, RuleTarget
from aira_common.permissions import Permission
from aira_gateway.anomalies.suspensions import AccessSuspension, as_dict
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.incidents.common import _body_of, router
from aira_gateway.api.reporting.common import visible_scope
from aira_gateway.auth.attribution import is_valid_use_case
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.state import sessionmaker_of, suspensions_of

#: What a suspension may name: `AccessSuspension.target_value`'s width, stated as a decision rather
#: than read off the column. Refused beyond it, never truncated — a shortened target matches nobody.
MAX_TARGET_VALUE = 255

#: The fastest a throttled target may go. The ceiling Management applies to a configured rate limit
#: (`ratelimits/models.py`), because a throttle is a rate limit written during an incident; it also
#: keeps the figure inside `throttle_rpm`'s 32-bit column.
MAX_THROTTLE_RPM = 1_000_000

#: The longest ``minutes`` may name. Not a policy limit — no ``minutes`` means "until lifted" — but
#: the bound that keeps ``now + timedelta(minutes=N)`` from overflowing. A century.
MAX_SUSPENSION_MINUTES = 100 * 365 * 24 * 60

#: How much of a caller's ``reason`` is stored.
MAX_REASON = 500

#: How many suspensions the list returns, newest first.
LISTED = 200


def _require_an_incident_role(principal: Principal) -> None:
    """Only an incident role may stop or restore traffic (`FRD-503` FR-6).

    The roles that may author a global rule (`FRD-500` FR-8), since a hand-made suspension is a
    global rule's effect without the rule. **Not** the oversight set: that includes IT Steuerung,
    which PRD §154 gives every figure and no write.
    """
    if principal.method == "demo":
        # Authentication is switched off: there is no identity to authorise.
        return
    if not principal.allows(Permission.INCIDENT_SUSPEND):
        raise GeminiHTTPError(
            403,
            "Suspending or restoring access needs the permission to stop traffic "
            "(incident.suspend).",
            "PERMISSION_DENIED",
        )


def _whole(body: dict[str, Any], field: str, *, minimum: int, maximum: int) -> int | None:
    """A caller's whole number within bounds, ``None`` where they sent none — a 400, never a 500.

    Python's integers are unbounded and what they end up in (a `timedelta`, an `Integer` column) is
    not. A bool is refused rather than read as 0/1: ``true`` here is a client bug.
    """
    value = body.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise GeminiHTTPError(400, f"'{field}' must be a whole number.", "INVALID_ARGUMENT")
    try:
        number = int(value)
    except ValueError as exc:
        raise GeminiHTTPError(
            400, f"'{field}' must be a whole number.", "INVALID_ARGUMENT"
        ) from exc
    if not minimum <= number <= maximum:
        raise GeminiHTTPError(
            400,
            f"'{field}' must be between {minimum} and {maximum}.",
            "INVALID_ARGUMENT",
        )
    return number


def _author(principal: Principal) -> str:
    """Who decided, as a reviewer reads it: the person's name, and the subject only for a token
    that carries none — ``user:9f1c3e2a-…`` names nobody a human can look up."""
    return f"user:{principal.person or principal.subject}"


def _concerning(use_case: str, principal: Principal) -> ColumnElement[bool]:
    """The stops that apply to ``principal`` inside ``use_case``, and nobody else's.

    - the whole use case: ``target = use_case`` naming it;
    - this caller: ``target = subject`` naming them, or ``target = credential`` naming their own key
      prefix — scoped to this use case or to everywhere.

    A stop on another person stays with the incident roles: that somebody else was blocked, and
    why, is theirs.
    """
    in_here = or_(AccessSuspension.use_case.is_(None), AccessSuspension.use_case == use_case)
    concerns: list[ColumnElement[bool]] = [
        and_(
            AccessSuspension.target == RuleTarget.USE_CASE.value,
            AccessSuspension.target_value == use_case,
        )
    ]
    # Either alphabet, as findings are matched: a directory id for an OIDC caller, a username for a
    # key.
    names = sorted({name for name in (principal.subject, principal.person) if name})
    if names:
        concerns.append(
            and_(
                in_here,
                AccessSuspension.target == RuleTarget.SUBJECT.value,
                AccessSuspension.target_value.in_(names),
            )
        )
    if principal.credential:
        concerns.append(
            and_(
                in_here,
                AccessSuspension.target == RuleTarget.CREDENTIAL.value,
                AccessSuspension.target_value == principal.credential,
            )
        )
    return or_(*concerns)


@router.get("/v1beta/suspensions")
async def list_suspensions(
    request: Request,
    principal: Principal = Depends(require_principal),
    use_case: str = Query("", max_length=64),
) -> JSONResponse:
    """What was stopped (`FRD-503` FR-8), to three audiences.

    Whoever may stop traffic, or reads every security finding, sees every stop — a finding already
    says what was done. A member of a use case asks with ``use_case`` and sees whether that use
    case, or they themselves, are stopped: the fact a member most needs when requests start
    answering 429. Anybody else is refused.
    """
    oversight = principal.allows(Permission.INCIDENT_SUSPEND) or principal.allows(
        Permission.ANOMALY_READ_ALL
    )
    stmt = select(AccessSuspension)
    if not oversight and principal.method != "demo":
        if not use_case:
            _require_an_incident_role(principal)
        scope = visible_scope(principal, Permission.ANOMALY_READ_ALL)
        if scope is not None and use_case not in scope:
            return JSONResponse({"suspensions": [], "scope": "own", "in_scope": False})
        stmt = stmt.where(_concerning(use_case, principal))
        audience = "own"
    else:
        if use_case:
            stmt = stmt.where(
                or_(
                    AccessSuspension.use_case == use_case,
                    and_(
                        AccessSuspension.target == RuleTarget.USE_CASE.value,
                        AccessSuspension.target_value == use_case,
                    ),
                )
            )
        audience = "all"
    stmt = stmt.order_by(AccessSuspension.created_at.desc()).limit(LISTED)
    async with sessionmaker_of(request)() as session:
        rows = list((await session.execute(stmt)).scalars().all())
    # Lifted and expired ones included: "who was blocked last Tuesday" is what an incident review
    # asks (`FRD-503` FR-8).
    return JSONResponse(
        {"suspensions": [as_dict(row) for row in rows], "scope": audience, "in_scope": True}
    )


@router.post("/v1beta/suspensions", status_code=201)
async def create_suspension(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """Stop, or throttle, one target's traffic (PRD §1.1 feature 7)."""
    _require_an_incident_role(principal)
    body = await _body_of(request)

    target = str(body.get("target") or "")
    if target not in {t.value for t in RuleTarget}:
        raise GeminiHTTPError(
            400,
            f"'target' must be one of: {', '.join(t.value for t in RuleTarget)}.",
            "INVALID_ARGUMENT",
        )
    value = str(body.get("target_value") or "").strip()
    if not value:
        raise GeminiHTTPError(400, "'target_value' is required.", "INVALID_ARGUMENT")
    if len(value) > MAX_TARGET_VALUE:
        raise GeminiHTTPError(
            400,
            f"'target_value' is limited to {MAX_TARGET_VALUE} characters.",
            "INVALID_ARGUMENT",
        )
    scope = str(body.get("use_case") or "").strip()
    if scope and not is_valid_use_case(scope):
        # The charset every caller-supplied slug meets (`ADR-0007`): a suspension scoped to a use
        # case that cannot exist stops nothing and looks active.
        raise GeminiHTTPError(400, "Invalid use case identifier.", "INVALID_ARGUMENT")
    action = str(body.get("action") or RuleAction.BLOCK.value)
    if action not in {RuleAction.BLOCK.value, RuleAction.THROTTLE.value}:
        raise GeminiHTTPError(400, "'action' must be 'block' or 'throttle'.", "INVALID_ARGUMENT")
    throttle_rpm = _whole(body, "throttle_rpm", minimum=1, maximum=MAX_THROTTLE_RPM)
    if action == RuleAction.THROTTLE.value and not throttle_rpm:
        raise GeminiHTTPError(400, "'throttle_rpm' is required for a throttle.", "INVALID_ARGUMENT")
    # At least one minute: a suspension that expired before it was stored would be listed as a kill
    # switch while stopping nothing (`FRD-125`).
    minutes = _whole(body, "minutes", minimum=1, maximum=MAX_SUSPENSION_MINUTES)

    row = AccessSuspension(
        use_case=scope or None,
        target=target,
        target_value=value,
        action=action,
        throttle_rpm=throttle_rpm,
        # A person may suspend indefinitely because a person can lift it; a rule always expires
        # (`ADR-0014` §2).
        expires_at=(datetime.now(UTC) + timedelta(minutes=minutes) if minutes else None),
        author=_author(principal),
        reason=str(body.get("reason") or "")[:MAX_REASON],
    )
    async with sessionmaker_of(request)() as session:
        session.add(row)
        await session.commit()
    suspensions_of(request).invalidate()
    return JSONResponse(as_dict(row), status_code=201)


@router.delete("/v1beta/suspensions/{suspension_id}")
async def lift_suspension(
    suspension_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """Lift one. The row is kept and stamped, never deleted (`FRD-503` FR-8)."""
    _require_an_incident_role(principal)
    async with sessionmaker_of(request)() as session:
        row = await session.get(AccessSuspension, suspension_id)
        if row is None:
            raise GeminiHTTPError(404, "No such suspension.", "NOT_FOUND")
        if row.lifted_at is None:
            row.lifted_at = datetime.now(UTC)
            row.lifted_by = _author(principal)
            await session.commit()
        payload = as_dict(row)
    suspensions_of(request).invalidate()
    return JSONResponse(payload)
