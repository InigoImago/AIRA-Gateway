"""Stopping traffic, and restoring it (`FRD-503`).

Its own module rather than a section of `reporting.py`, and an architecture assertion is what said
so: every endpoint there resolves `visible_scope` exactly once, and these resolve it **zero** times
— correctly, because they are bounded by *role* rather than by use case. Two different ways of
being safe do not belong behind one heading.

Deliberately **not** routed through Management and Kafka like every other piece of configuration:
an incident control that depends on the event bus fails exactly when the bus is the problem, and
"traffic is doing something alarming" and "the pipeline between the planes is unhealthy" are not
independent events (§4.3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from aira_common.anomalies import RuleAction, RuleTarget
from aira_gateway.anomalies.suspensions import AccessSuspension, as_dict
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal

router = APIRouter(tags=["incidents"])


def _require_oversight(principal: Principal) -> None:
    """Only an oversight role may stop traffic by hand (`FRD-503` FR-6).

    The same roles that may author a global rule (`FRD-500` FR-8), for the same reason: a
    hand-made suspension is a global rule's effect without the rule.
    """
    if principal.method == "demo":
        # Authentication is switched off entirely; there is no identity to authorise, and the
        # demo mode is not a place to invent one. The same call `visible_scope` makes above, for
        # the same reason — a deployment that has declared it is not checking identity cannot then
        # be asked to check a role.
        return
    if not principal.is_oversight:
        raise GeminiHTTPError(
            403,
            "Only IT Security or a Global Administrator may suspend or restore access.",
            "PERMISSION_DENIED",
        )


@router.get("/v1beta/suspensions")
async def list_suspensions(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    _require_oversight(principal)
    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        stmt = select(AccessSuspension).order_by(AccessSuspension.created_at.desc()).limit(200)
        rows = list((await session.execute(stmt)).scalars().all())
    # Lifted and expired ones are included: "this caller was blocked for two hours last Tuesday"
    # is exactly the question an incident review asks (`FRD-503` FR-8).
    return JSONResponse({"suspensions": [as_dict(row) for row in rows]})


@router.post("/v1beta/suspensions", status_code=201)
async def create_suspension(
    request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """The kill switch (PRD §1.1 feature 7).

    Deliberately **not** routed through Management and Kafka like every other piece of
    configuration: an incident control that depends on the event bus fails exactly when the bus is
    the problem, and "traffic is doing something alarming" and "the pipeline between the planes is
    unhealthy" are not independent events (`FRD-503` §4.3).
    """
    _require_oversight(principal)
    body = await request.json()
    if not isinstance(body, dict):
        raise GeminiHTTPError(400, "Send one suspension.", "INVALID_ARGUMENT")

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
    action = str(body.get("action") or RuleAction.BLOCK.value)
    if action not in {RuleAction.BLOCK.value, RuleAction.THROTTLE.value}:
        raise GeminiHTTPError(400, "'action' must be 'block' or 'throttle'.", "INVALID_ARGUMENT")
    throttle_rpm = body.get("throttle_rpm")
    if action == RuleAction.THROTTLE.value and not throttle_rpm:
        raise GeminiHTTPError(400, "'throttle_rpm' is required for a throttle.", "INVALID_ARGUMENT")

    minutes = body.get("minutes")
    row = AccessSuspension(
        use_case=body.get("use_case") or None,
        target=target,
        target_value=value,
        action=action,
        throttle_rpm=int(throttle_rpm) if throttle_rpm else None,
        # A person may suspend indefinitely, because a person can also lift it. A rule cannot,
        # which is why an automatic one always expires (`ADR-0014` §2).
        expires_at=(datetime.now(UTC) + timedelta(minutes=int(minutes)) if minutes else None),
        author=f"user:{principal.subject}",
        reason=str(body.get("reason") or "")[:500],
    )
    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        session.add(row)
        await session.commit()
    request.app.state.suspensions.invalidate()
    return JSONResponse(as_dict(row), status_code=201)


@router.delete("/v1beta/suspensions/{suspension_id}")
async def lift_suspension(
    suspension_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """Lift one. The row is kept and stamped, never deleted (`FRD-503` FR-8)."""
    _require_oversight(principal)
    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        row = await session.get(AccessSuspension, suspension_id)
        if row is None:
            raise GeminiHTTPError(404, "No such suspension.", "NOT_FOUND")
        if row.lifted_at is None:
            row.lifted_at = datetime.now(UTC)
            row.lifted_by = f"user:{principal.subject}"
            await session.commit()
        payload = as_dict(row)
    request.app.state.suspensions.invalidate()
    return JSONResponse(payload)
