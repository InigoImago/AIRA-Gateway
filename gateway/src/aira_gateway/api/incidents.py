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

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from aira_common.anomalies import RuleAction, RuleTarget
from aira_gateway.anomalies.suspensions import AccessSuspension, as_dict
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelCatalog
from aira_gateway.state import sessionmaker_of, suspensions_of
from aira_gateway.upstreams.base import ProviderRegistry

#: A check must be quick enough that somebody presses the button and waits for it.
MODEL_CHECK_TIMEOUT_SECONDS = 5.0

router = APIRouter(tags=["incidents"])


def _require_oversight(principal: Principal) -> None:
    """Only an incident role may stop traffic by hand (`FRD-503` FR-6).

    The same roles that may author a global rule (`FRD-500` FR-8), for the same reason: a
    hand-made suspension is a global rule's effect without the rule. **Not** the oversight set —
    that is a visibility predicate, and it includes IT Steuerung, which PRD §154 gives every figure
    and no write anywhere. Asking the two planes the same question and getting different answers is
    how this was found.
    """
    if principal.method == "demo":
        # Authentication is switched off entirely; there is no identity to authorise, and the
        # demo mode is not a place to invent one. The same call `visible_scope` makes above, for
        # the same reason — a deployment that has declared it is not checking identity cannot then
        # be asked to check a role.
        return
    if not principal.may_act_on_incidents:
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
    sessionmaker = sessionmaker_of(request)
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
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        session.add(row)
        await session.commit()
    suspensions_of(request).invalidate()
    return JSONResponse(as_dict(row), status_code=201)


@router.delete("/v1beta/suspensions/{suspension_id}")
async def lift_suspension(
    suspension_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    """Lift one. The row is kept and stamped, never deleted (`FRD-503` FR-8)."""
    _require_oversight(principal)
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        row = await session.get(AccessSuspension, suspension_id)
        if row is None:
            raise GeminiHTTPError(404, "No such suspension.", "NOT_FOUND")
        if row.lifted_at is None:
            row.lifted_at = datetime.now(UTC)
            row.lifted_by = f"user:{principal.subject}"
            await session.commit()
        payload = as_dict(row)
    suspensions_of(request).invalidate()
    return JSONResponse(payload)


# ═══ can this model be reached at all? (FRD-506) ════════════════════════════════════════════════
#
# The question the console could not answer. A catalog entry is a **declaration** — it says what a
# model costs and what it may be asked to do — and declaring one requires no credential and proves
# nothing. Without a key no adapter is registered, so the model sits in the catalog looking
# perfectly healthy and every request for it comes back `model_not_found`, which reads as a typo
# rather than as a missing credential.
#
# So three separate answers, never collapsed into "ok":
#
#   declared    — somebody wrote it down. Says nothing about reachability.
#   served      — an adapter is registered for it. This is the one a missing key fails.
#   reachable   — the adapter's cheap remote question answered.
#
# **Never a generation.** `FRD-117` settled this for the readiness probe and it holds here for the
# same reason: a self-deployed model can be scaled to zero, and "check whether it works" must not
# be the thing that wakes it, bills for it, and waits minutes to say so.


@router.get("/v1beta/models/{model:path}:check")
async def check_model(
    request: Request,
    model: str,
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """Whether a declared model is actually served, and whether its provider answers.

    Bounded by role rather than by use case: it describes the *installation*, not anybody's
    traffic, and the people who need it are the ones who declare models and the ones who
    investigate why a use case cannot reach one.
    """
    if not principal.may_act_on_incidents:
        raise GeminiHTTPError(
            403,
            "Checking a model is available to IT Security and Global Administrators.",
            "PERMISSION_DENIED",
        )

    catalog: ModelCatalog = request.app.state.catalog
    # Annotated like its neighbour above. It was the one service read in the gateway that was not,
    # and the annotation is the only thing that makes the call below type-checked at all — see
    # `state.py` for what an unchecked one cost.
    registry: ProviderRegistry = request.app.state.providers
    declaration = await catalog.declaration(model)
    provider = registry.provider_for(model)

    result: dict[str, Any] = {
        "model": model,
        "declared": bool(declaration and declaration.declared),
        "served": provider is not None,
        "reachable": None,
        "detail": "",
    }

    if provider is None:
        # The case a missing credential produces, and the one worth naming precisely: an adapter is
        # registered only when its credential is configured, so "declared but not served" is
        # almost always "nobody gave this installation a key for it".
        result["detail"] = (
            "No upstream serves this model. A declaration is metadata; reaching a model needs a "
            "provider, and a provider is registered only when its credential is configured."
        )
        return JSONResponse(result)

    ping = getattr(provider, "ping", None)
    if ping is None:
        # Said, not assumed — the `FRD-117` rule. "We did not look" and "it is fine" are different
        # answers and only one of them is safe to act on.
        result["detail"] = "This upstream offers nothing cheap to ask; it was not contacted."
        return JSONResponse(result)

    try:
        detail = await asyncio.wait_for(ping(), timeout=MODEL_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        result["reachable"] = False
        result["detail"] = f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s."
    except Exception as exc:  # noqa: BLE001 — anything here means "not reachable"
        result["reachable"] = False
        # The type, not the message: a provider's error text can carry a URL with a key in it.
        result["detail"] = f"Not reachable ({type(exc).__name__})."
    else:
        result["reachable"] = True
        result["detail"] = str(detail)
    return JSONResponse(result)
