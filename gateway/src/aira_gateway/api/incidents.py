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
from aira_gateway.auth.attribution import is_valid_use_case
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelCatalog
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, Thinking
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.state import sessionmaker_of, suspensions_of
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamError

#: A check must be quick enough that somebody presses the button and waits for it.
MODEL_CHECK_TIMEOUT_SECONDS = 5.0

#: What a suspension may name, matching `AccessSuspension.target_value`'s column. Kept beside the
#: check rather than read off the model: a bound the caller is told about is a decision, and one
#: derived from a column is a coincidence that changes when somebody widens the column.
MAX_TARGET_VALUE = 255

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
    # Bounded here, where the caller can still be told which field is wrong. `reason` two lines
    # below has been truncated since it was written; this one was not, so a value longer than the
    # column turned a caller's mistake into a `StringDataRightTruncation` on Postgres and a **500**
    # — on the endpoint somebody reaches for during an incident. Refused rather than truncated: a
    # silently shortened target matches nobody, which is a kill switch that reports success and
    # stops nothing.
    if len(value) > MAX_TARGET_VALUE:
        raise GeminiHTTPError(
            400,
            f"'target_value' is limited to {MAX_TARGET_VALUE} characters.",
            "INVALID_ARGUMENT",
        )
    scope = str(body.get("use_case") or "").strip()
    if scope and not is_valid_use_case(scope):
        # Same charset as everywhere else a slug arrives from a caller (`ADR-0007`). An unchecked
        # one reaches the audit trail, the console's filters and `_matches`, where it can only ever
        # fail to match — a suspension scoped to a use case that cannot exist stops nothing and
        # looks active.
        raise GeminiHTTPError(400, "Invalid use case identifier.", "INVALID_ARGUMENT")
    action = str(body.get("action") or RuleAction.BLOCK.value)
    if action not in {RuleAction.BLOCK.value, RuleAction.THROTTLE.value}:
        raise GeminiHTTPError(400, "'action' must be 'block' or 'throttle'.", "INVALID_ARGUMENT")
    throttle_rpm = body.get("throttle_rpm")
    if action == RuleAction.THROTTLE.value and not throttle_rpm:
        raise GeminiHTTPError(400, "'throttle_rpm' is required for a throttle.", "INVALID_ARGUMENT")

    minutes = body.get("minutes")
    row = AccessSuspension(
        use_case=scope or None,
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
    provider: str = "",
    publisher: str = "",
    region: str = "",
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """Whether a model is actually served, and whether its provider answers.

    Bounded by role rather than by use case: it describes the *installation*, not anybody's
    traffic, and the people who need it are the ones who declare models and the ones who
    investigate why a use case cannot reach one.

    **The three provenance parameters are what makes this answer the question being asked.**
    Without them this read the *saved* catalogue row, and the console's button sits in an editor
    where somebody has just changed the provider — so a reader who corrected `generative-language`
    to `vertex`, typed a region, and pressed Check was told *"Declared, but nothing serves it"*
    about the row they were in the middle of replacing. A correct answer about a configuration
    nobody was asking about, which is the same shape as a verdict left over from another model:
    right, and wearing the wrong label.

    Reported after exactly that: *"gemini 3.5 flash does not work in the interface, I get the error
    message"* — with the form already holding the values that do work. Passed explicitly rather
    than inferred, so this endpoint keeps answering about the saved row when nobody overrides it.
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
    # The catalogue's provider **and publisher**, like every other resolution. This asked with the
    # name alone, so the one control an administrator presses to find out whether a model works
    # answered "not served" for a model the gateway was serving — the fifth site to learn the pair,
    # and the one a person actually looks at.
    # What the caller is asking about, which is the form's state where they gave one.
    asked_provider = provider or declaration.provider
    asked_publisher = publisher or declaration.publisher
    asked_regions = _regions_asked(region, declaration)
    upstream = registry.provider_for(model, asked_provider, asked_publisher)

    result: dict[str, Any] = {
        "model": model,
        "declared": bool(declaration and declaration.declared),
        "served": upstream is not None,
        "reachable": None,
        "detail": "",
        #: One verdict per region, because a model in several places is reachable in some of them
        #: and not others — which is the whole reason somebody lists more than one (`FRD-609`).
        "regions": [],
    }

    if upstream is None:
        # The case a missing credential produces, and the one worth naming precisely: an adapter is
        # registered only when its credential is configured, so "declared but not served" is
        # almost always "nobody gave this installation a key for it".
        # **Two causes, and naming only one sends people to the wrong system.** This said the
        # credential must be missing, which is right for AI Studio and wrong for Agent Platform and
        # Azure: there the credential can be perfectly configured and the *model* simply is not in
        # `AIRA_VERTEX_MODELS` / `AIRA_FOUNDRY_DEPLOYMENTS`. Reported by somebody whose key worked
        # and whose model still answered "not served".
        result["detail"] = (
            "No upstream serves this model. A declaration is metadata; reaching a model needs a "
            "provider that offers it — most often because no credential is configured for its "
            "platform, and otherwise because the catalogue entry names a provider no adapter "
            "claims. Check the provider and publisher on the model: on a platform that hosts two "
            "dialects the publisher is what selects one."
        )
        return JSONResponse(result)

    ping = getattr(upstream, "ping", None)
    if ping is None:
        # Said, not assumed — the `FRD-117` rule. "We did not look" and "it is fine" are different
        # answers and only one of them is safe to act on.
        result["detail"] = "This upstream offers nothing cheap to ask; it was not contacted."
        return JSONResponse(result)

    # **Every region, not the first.** A model catalogued in three places is reachable in some and
    # not others, and answering about one of them would be an answer to a question nobody asked —
    # the same shape as reporting about whichever model an adapter had configured first, which is
    # the defect the `model` argument to `ping` was added for.
    #
    # `:countTokens` costs nothing, so the number of regions bounds nothing but time.
    for asked_region in asked_regions or [""]:
        verdict = await _reach(ping, model, asked_region)
        result["regions"].append({"region": asked_region, **verdict})

    # The summary is the **best** of them, and the list beside it says which. A model that answers
    # in one of its three regions *is* reachable — the request will be served — and a summary of
    # "not reachable" would be false. The two together are what an administrator needs: it works,
    # and here is the one that does not.
    reachable = [entry for entry in result["regions"] if entry["reachable"]]
    best = reachable[0] if reachable else result["regions"][0]
    result["reachable"] = best["reachable"]
    result["detail"] = best["detail"]
    if reachable and len(reachable) != len(result["regions"]):
        unreachable = [entry["region"] for entry in result["regions"] if not entry["reachable"]]
        result["detail"] += f" Not reachable in: {', '.join(unreachable)}."
    return JSONResponse(result)


def _regions_asked(region: str, declaration: Any) -> list[str]:
    """Which regions to check: the form's, where it gave any, otherwise the catalogue's."""
    if region:
        return [part.strip() for part in region.split(",") if part.strip()]
    return list(getattr(declaration, "regions", ()) or [])


async def _reach(ping: Any, model: str, region: str) -> dict[str, Any]:
    """Ask one region whether it has this model, and say what happened in its own words."""
    addressing = {"regions": [region]} if region else {}
    try:
        detail = await asyncio.wait_for(
            ping(model, addressing), timeout=MODEL_CHECK_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return {
            "reachable": False,
            "detail": f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s.",
        }
    except RegionNotAllowed as exc:
        # Named apart from an unreachable one, because the two need different actions: this one is
        # fixed in `AIRA_ALLOWED_REGIONS` or by not listing the region, never by the provider.
        return {"reachable": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — anything else here means "not reachable"
        # The type, not the message: a provider's error text can carry a URL with a key in it.
        return {"reachable": False, "detail": f"Not reachable ({type(exc).__name__})."}
    return {"reachable": True, "detail": str(detail)}


#: How many words one check may ask about. A level list is a handful — Gemini 3 has two — and this
#: endpoint spends real tokens, so an unbounded list is an unbounded bill from one button press.
MAX_LEVELS_PER_CHECK = 12

#: The smallest generation this can be. A refused word costs nothing (the provider answers 400
#: before generating); an accepted one costs this. Measured on 2026-08-19 against Vertex:
#: `generateContent` with `maxOutputTokens: 1` billed exactly one output token.
_PROBE_OUTPUT_TOKENS = 1


@router.post("/v1beta/models/{model:path}:checkThinking")
async def check_thinking_levels(
    request: Request,
    model: str,
    provider: str = "",
    publisher: str = "",
    region: str = "",
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """Ask the model itself whether it accepts each declared level word (`ADR-0021`).

    **The question the catalog cannot answer.** A level is now a word the vendor takes, typed
    freely, because no closed list survives the vendors' next release — and the cost of free text
    is that a typo, or a word from the wrong family, looks exactly like a working declaration until
    a caller's request comes back 400. So the console asks the model, and the provider's own
    refusal is the answer: *"thinking_level is not supported by this model"*.

    Measured before it was built, because "can this be checked cheaply" decided whether it was
    worth building. `:countTokens` is free and **useless here** — it answers 200 to an unsupported
    level and to an out-of-range budget alike, because it never looks at `generationConfig`. A
    capped `generateContent` does judge, and it is nearly free: a word the model rejects is refused
    before any generation, and a word it accepts costs one output token.

    Informs, never blocks — `FRD-506`'s shape. A red word is a word to look at, not a save that is
    refused: the model may be temporarily unreachable, and the catalog is Management's.
    """
    if not principal.may_act_on_incidents:
        raise GeminiHTTPError(
            403,
            "Checking a model is available to IT Security and Global Administrators.",
            "PERMISSION_DENIED",
        )

    body = await request.json() if await request.body() else {}
    asked = body.get("levels") if isinstance(body, dict) else None
    words = (
        [w.strip().lower() for w in asked if isinstance(w, str) and w.strip()][
            :MAX_LEVELS_PER_CHECK
        ]
        if isinstance(asked, list)
        else []
    )
    if not words:
        raise GeminiHTTPError(400, "Name at least one level word to check.", "INVALID_ARGUMENT")

    catalog: ModelCatalog = request.app.state.catalog
    registry: ProviderRegistry = request.app.state.providers
    declaration = await catalog.declaration(model)
    # The same override as `check_model` above, for the same reason: this button sits in an editor
    # whose provenance may not be saved yet, and an answer about the stored row is an answer to a
    # question nobody asked.
    upstream = registry.provider_for(
        model, provider or declaration.provider, publisher or declaration.publisher
    )
    if upstream is None:
        raise GeminiHTTPError(
            400,
            "No upstream serves this model, so there is nobody to ask about its levels. Save the "
            "model and check its reachability first.",
            "FAILED_PRECONDITION",
        )
    if not getattr(upstream, "expresses_thinking_levels", False):
        # Answered without spending anything: this dialect has no field for a level at all, so
        # every word would be refused for the same reason and none of it is about the model.
        return JSONResponse(
            {
                "model": model,
                "results": [
                    {
                        "region": "",
                        "level": word,
                        "accepted": False,
                        "detail": (
                            "This model's dialect asks for thinking by naming a token budget and "
                            "has no field for a level word. Use 'limited' instead."
                        ),
                    }
                    for word in words
                ],
            }
        )

    # **Every word in every region.** A model may be catalogued in several places, and which
    # thinking words a place accepts is not knowable from here: Google rolls a family out region by
    # region, so `thinkingLevel` can work in one and answer *"not supported by this model"* in
    # another, and a declaration checked in one region would be a claim about the others.
    #
    # It costs one output token per accepted word per region, and nothing at all for a refused one
    # — the refusal precedes any generation. `MAX_LEVELS_PER_CHECK` bounds the words; the regions
    # are bounded by what somebody typed into the catalogue.
    asked_regions = _regions_asked(region, declaration) or [""]
    results = []
    for asked_region in asked_regions:
        for word in words:
            results.append(
                {
                    "region": asked_region,
                    "level": word,
                    **await _accepts(upstream, model, word, asked_region),
                }
            )

    return JSONResponse({"model": model, "results": results})


async def _accepts(upstream: Any, model: str, word: str, region: str) -> dict[str, Any]:
    """Ask one model in one region whether it takes one level word."""
    probe = CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        max_output_tokens=_PROBE_OUTPUT_TOKENS,
        thinking=Thinking(mode=word),
        addressing={"regions": [region]} if region else {},
    )
    try:
        await asyncio.wait_for(upstream.generate(probe), timeout=MODEL_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return {
            "accepted": False,
            "detail": f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s.",
        }
    except RegionNotAllowed as exc:
        return {"accepted": False, "detail": str(exc)}
    except (UpstreamError, DialectUnsupported) as exc:
        # **The provider's own words**, which is the whole value of this button: Google says
        # *"thinking_level is not supported by this model"*, and no rule in this repository could
        # have said it as precisely or stayed as true.
        return {"accepted": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — anything else means "we could not tell"
        # The type, not the message: an arbitrary provider error can carry a URL with a key.
        return {"accepted": False, "detail": f"Could not ask ({type(exc).__name__})."}
    return {"accepted": True, "detail": "The model accepted it."}
