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
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from aira_common.anomalies import RuleAction, RuleTarget
from aira_common.models import ThinkingMode
from aira_gateway.anomalies.suspensions import AccessSuspension, as_dict
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.audit import Outcome
from aira_gateway.auth.attribution import Attribution, is_valid_use_case
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import ModelCatalog
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
    Thinking,
)
from aira_gateway.persistence.recorder import record_request
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.state import pricing_of, sessionmaker_of, suspensions_of
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamError

#: A check must be quick enough that somebody presses the button and waits for it.
MODEL_CHECK_TIMEOUT_SECONDS = 5.0

#: What a suspension may name, matching `AccessSuspension.target_value`'s column. Kept beside the
#: check rather than read off the model: a bound the caller is told about is a decision, and one
#: derived from a column is a coincidence that changes when somebody widens the column.
MAX_TARGET_VALUE = 255

#: The fastest a throttled target may be allowed to go. **The same ceiling Management applies to a
#: configured rate limit** (`ratelimits/models.py`, `MaxValueValidator(1_000_000)`), because a
#: throttle *is* a rate limit — one written during an incident instead of in the console — and two
#: ceilings for one concept is a difference nobody can explain at 03:00.
#:
#: A bound rather than a formality: `throttle_rpm` is an `Integer` column, which is 32-bit on
#: Postgres, so an unbounded figure is a `NumericValueOutOfRange` *after* the caller was told the
#: request was fine — a caller's own value arriving as a server error.
MAX_THROTTLE_RPM = 1_000_000

#: The longest a ``minutes`` may name. **Not a policy limit**: a person may already suspend
#: indefinitely by sending no ``minutes`` at all, and that is the documented way to say *until I
#: lift it*. This exists so the arithmetic cannot fail: ``datetime.now(UTC) + timedelta(minutes=N)``
#: raises `OverflowError` long before Python's unbounded integers run out, and it did: `10**30`
#: answered `500 Python int too large to convert to C int`. A century is longer than any incident
#: and comfortably inside what a `datetime` column holds.
MAX_SUSPENSION_MINUTES = 100 * 365 * 24 * 60

_log = structlog.get_logger(__name__)

router = APIRouter(tags=["incidents"])


async def _body_of(request: Request, *, optional: bool = False) -> dict[str, Any]:
    """The JSON object a caller sent, or a **400** naming what is wrong with it.

    `await request.json()` raises `JSONDecodeError` — a `ValueError` — and nothing here caught it,
    so `{`, an empty body and a couple of stray bytes each answered `500 Internal error` on the two
    endpoints somebody reaches for during an incident. Every other route in this project that reads
    a body already does this (`api/pipeline.py`, both surfaces): the rule was stated three times and
    held in three places, and these two were written afterwards.

    `optional` keeps `:checkThinking`'s existing behaviour, where sending nothing at all means "ask
    about everything the catalogue declares".
    """
    raw = await request.body()
    if not raw and optional:
        return {}
    try:
        body = await request.json()
    except ValueError as exc:
        raise GeminiHTTPError(400, "Request body is not valid JSON.", "INVALID_ARGUMENT") from exc
    if not isinstance(body, dict):
        raise GeminiHTTPError(400, "Send one JSON object.", "INVALID_ARGUMENT")
    return body


def _whole(body: dict[str, Any], field: str, *, minimum: int, maximum: int) -> int | None:
    """A caller's whole number, or ``None`` where they said nothing — never an exception.

    `int(body.get(field))` was written at both call sites below and neither survives a caller: a
    word answers `ValueError: invalid literal for int()`, and a number wider than a C `int` answers
    `OverflowError`, both as a **500**. Python's integers are unbounded and the things they end up
    in are not — a `timedelta`, an `Integer` column — which is the boundary rule this project has
    already paid for three times over (`LESSONS.md` §1).

    A bool is refused rather than read as 0/1: `true` in this field is a client bug, and silently
    throttling somebody to one request a minute because of it is the wrong way to find out.
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
    # Read through one parser that cannot raise past the caller (see `_whole`). `int(...)` stood
    # here and answered `500` for a word and for a number wider than a C `int`.
    throttle_rpm = _whole(body, "throttle_rpm", minimum=1, maximum=MAX_THROTTLE_RPM)
    if action == RuleAction.THROTTLE.value and not throttle_rpm:
        raise GeminiHTTPError(400, "'throttle_rpm' is required for a throttle.", "INVALID_ARGUMENT")

    # **At least one minute.** A zero or a negative writes a suspension that expired before it was
    # stored — `_still_applies` drops it on the very next read — so the console would list a kill
    # switch that stops nothing, which is the badge-wearing absent control `FRD-125` is about. It
    # was accepted silently; saying "no" is the only answer that leaves the operator informed.
    minutes = _whole(body, "minutes", minimum=1, maximum=MAX_SUSPENSION_MINUTES)
    row = AccessSuspension(
        use_case=scope or None,
        target=target,
        target_value=value,
        action=action,
        throttle_rpm=throttle_rpm,
        # A person may suspend indefinitely, because a person can also lift it. A rule cannot,
        # which is why an automatic one always expires (`ADR-0014` §2).
        expires_at=(datetime.now(UTC) + timedelta(minutes=minutes) if minutes else None),
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
    _attribute_diagnostic(request, principal)
    for asked_region in asked_regions or [""]:
        started = time.monotonic()
        verdict = await _reach(ping, model, asked_region)
        # Recorded although `:countTokens` is free, and the zero is the point: an auditor asking
        # *"who probed this model, and when"* gets an answer, and a row that says nothing was spent
        # is a stronger statement than no row at all.
        await _record_diagnostic(
            request,
            principal,
            operation="models:check",
            model=model,
            region=asked_region,
            usage=None,
            status=200 if verdict["reachable"] else 502,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
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


async def _record_diagnostic(
    request: Request,
    principal: Principal,
    *,
    operation: str,
    model: str,
    region: str,
    usage: CanonicalUsage | None,
    status: int,
    latency_ms: int,
) -> None:
    """Leave an audit row for a check somebody pressed (`FRD-610`).

    **These calls spend money, so they belong in the trail.** They were exempt, and the exemption
    was mine and wrong: the argument — bounded, role-gated, a token at a time — is true and answers
    a different question. *"How much did this cost"* has to be answerable, and a small amount
    nobody can see is not a small amount, it is an invisible one.

    Three things make the row honest rather than decorative:

    - **No use case, and none invented.** The check exists for a model that is not released to
      anybody yet — that is what it is *for* — so there is nothing to attribute it to. An audit row
      with no use case is an existing, supported shape here (unbound break-glass keys, demo
      traffic), and inventing an owner so a row has somewhere to sit is the failure `FRD-403`
      names.
    - **`Outcome.DIAGNOSTIC`, not `served`.** Counted as served, these would inflate every request
      figure with traffic no use case made — the shape `FRD-125b` refused for pipeline calls. Its
      own value is also what makes *"what did diagnostics cost this month"* a question somebody can
      ask.
    - **The real usage and the real price**, from the answer and the catalogue, so the figure is
      what was spent rather than what was estimated.

    Never raises. A diagnostic that fails because its bookkeeping failed would be the worst of both
    — no answer *and* no row — so a persistence failure is logged and the verdict still returns.
    """
    try:
        cost = await pricing_of(request).cost_nanos(model, usage) if usage else None
        await record_request(
            request,
            operation=operation,
            model=model,
            status=status,
            usage=usage,
            latency_ms=latency_ms,
            request_payload=None,
            response_payload=None,
            cost_nanos=cost,
            outcome=Outcome.DIAGNOSTIC,
            provenance=("", "", region),
            api="console",
        )
    except Exception:  # noqa: BLE001 — the verdict matters more than its bookkeeping
        _log.warning(
            "diagnostic_not_recorded",
            model=model,
            operation=operation,
            subject=principal.subject,
        )


def _attribute_diagnostic(request: Request, principal: Principal) -> None:
    """Attribute the check to the person who pressed it, and to **no use case**.

    `require_principal` authenticates and stops there; `record_request` reads
    `request.state.attribution`. Set here rather than by widening the dependency, because widening
    it would make every diagnostic *look like* a use case's request one refactor later.
    """
    request.state.attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        use_case=None,
        credential=principal.credential,
        username=principal.username,
    )


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

    body = await _body_of(request, optional=True)
    asked = body.get("levels") if isinstance(body, dict) else None
    words = (
        [w.strip().lower() for w in asked if isinstance(w, str) and w.strip()][
            :MAX_LEVELS_PER_CHECK
        ]
        if isinstance(asked, list)
        else []
    )
    asked_modes = body.get("modes") if isinstance(body, dict) else None
    modes = (
        [m.strip().lower() for m in asked_modes if isinstance(m, str) and m.strip()][
            :MAX_LEVELS_PER_CHECK
        ]
        if isinstance(asked_modes, list)
        else []
    )
    if not words and not modes:
        raise GeminiHTTPError(
            400, "Name at least one level word or mode to check.", "INVALID_ARGUMENT"
        )

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
    # **The modes, answered from the dialect and for nothing.** Every adapter declares
    # `thinking_modes` — the OpenAI family excludes `limited` and `auto`, Anthropic excludes
    # `auto` — and until now **nothing read it**: a declaration made by four adapters, asserted by
    # one test, and consulted by no code on any path. So a Global Administrator could tick `auto`
    # for a model on an OpenAI-dialect endpoint, be told nothing, and turn every thinking request
    # into a refusal that only shows up in production. Measured on the running stack on
    # 2026-08-20, where it was a `500`.
    #
    # No request is sent: the dialect either has the field or it does not, and that is not a
    # question about the model. Which is also why it is answered before the level branch below —
    # a dialect with no field for a *word* still has one for `disabled`.
    mode_results = [
        {
            "mode": mode,
            "accepted": mode in _expressible_modes(upstream),
            "detail": (
                "This model's wire format can express it."
                if mode in _expressible_modes(upstream)
                else (
                    f"This model's wire format has no way to say '{mode}'. Declaring it here "
                    "means every request that asks for it is refused — the model is never "
                    "reached."
                )
            ),
        }
        for mode in modes
    ]

    # No early return for "modes only". There was one, and a mutation proved it changed nothing:
    # with no words, both branches below produce an empty `results` list and neither sends a
    # request — so it was a second statement of a rule the code already made. This repository has
    # deleted three of those; a rule written twice is one that can be corrected in one place.
    if not getattr(upstream, "expresses_thinking_levels", False):
        # Answered without spending anything: this dialect has no field for a level at all, so
        # every word would be refused for the same reason and none of it is about the model.
        return JSONResponse(
            {
                "model": model,
                "modes": mode_results,
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
    _attribute_diagnostic(request, principal)
    results = []
    for asked_region in asked_regions:
        for word in words:
            started = time.monotonic()
            verdict, usage = await _accepts(upstream, model, word, asked_region)
            # **The row that closes the exemption.** A word the model accepts costs an output
            # token; one it refuses costs nothing, and both leave a record naming what was spent.
            await _record_diagnostic(
                request,
                principal,
                operation=f"models:checkThinking:{word}",
                model=model,
                region=asked_region,
                usage=usage,
                status=200 if verdict["accepted"] else 400,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            results.append({"region": asked_region, "level": word, **verdict})

    return JSONResponse({"model": model, "results": results, "modes": mode_results})


def _expressible_modes(upstream: Any) -> set[str]:
    """Which thinking modes this dialect has a field for, as the words the console sends.

    `getattr` with a permissive default, deliberately: an adapter that declares nothing is treated
    as able to express everything, which keeps this button *informing* rather than inventing red
    marks about a stand-in or a provider written before the flag existed. The runtime refusal is
    the backstop either way — `DialectUnsupported` is in `REFUSALS` and answers 400 by name.
    """
    declared = getattr(upstream, "thinking_modes", None)
    if not declared:
        return {str(mode) for mode in ThinkingMode}
    return {str(mode) for mode in declared}


async def _accepts(
    upstream: Any, model: str, word: str, region: str
) -> tuple[dict[str, Any], CanonicalUsage | None]:
    """Ask one model in one region whether it takes one level word, and say what it cost.

    The usage travels back with the verdict because the answer is the only place it exists — this
    used to discard the response entirely, which is how the spend became invisible.
    """
    probe = CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        max_output_tokens=_PROBE_OUTPUT_TOKENS,
        thinking=Thinking(mode=word),
        addressing={"regions": [region]} if region else {},
    )
    try:
        answer = await asyncio.wait_for(
            upstream.generate(probe), timeout=MODEL_CHECK_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return {
            "accepted": False,
            "detail": f"Did not answer within {MODEL_CHECK_TIMEOUT_SECONDS:g}s.",
        }, None
    except RegionNotAllowed as exc:
        return {"accepted": False, "detail": str(exc)}, None
    except (UpstreamError, DialectUnsupported) as exc:
        # **The provider's own words**, which is the whole value of this button: Google says
        # *"thinking_level is not supported by this model"*, and no rule in this repository could
        # have said it as precisely or stayed as true.
        return {"accepted": False, "detail": str(exc)}, None
    except Exception as exc:  # noqa: BLE001 — anything else means "we could not tell"
        # The type, not the message: an arbitrary provider error can carry a URL with a key.
        return {"accepted": False, "detail": f"Could not ask ({type(exc).__name__})."}, None
    return {"accepted": True, "detail": "The model accepted it."}, getattr(answer, "usage", None)
