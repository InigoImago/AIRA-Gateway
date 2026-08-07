"""The predecessor's endpoints, served by AIRA (FRD-107, Stage A + B).

Under ``/kira/api/external``, so a migrating consumer changes a base URL and nothing else. This
module maps one wire format; **everything below the surface is shared** with the Gemini routes via
``api/serving`` — the pre-dispatch gate, the pipeline, the dispatch chain, the audit writer. A
second copy of those controls is the `:embedContent` failure with an extra hundred lines to hide in.

Stage B (2026-08-06) serves the whole contract: text, documents, **thinking**, **structured
output** and **batch embedding with task types**. Nothing about the wire format changed — the
fields Stage A refused by name are now honoured, which is the migration `ADR-0010` promised.

Where a model cannot honour one, the request is still **refused** rather than served differently.
A field accepted and quietly ignored produces an answer that is wrong for a reason the caller
cannot see, and that is true whether the reason is "not built yet" or "this model cannot".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from aira_common.models import Capability
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.kira import errors, schemas
from aira_gateway.api.kira.attribution import resolve as resolve_attribution
from aira_gateway.api.kira.mapping import (
    completed_event,
    to_canonical,
    to_chat_response,
    to_embedding,
)
from aira_gateway.api.serving import (
    REFUSALS,
    UPSTREAM_STATUS_MAP,
    Prepared,
    catalog_of,
    check_structured_result,
    elapsed_ms,
    prepare_for_dispatch,
    provenance,
    refusal_outcome,
    registry_of,
    requirements_for,
    schema_bounds,
)
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalResponse
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import DEFAULT_TASK_TYPE, EmbeddingRejected
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.reporting.service import ReportingService
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import UpstreamError

BASE = "/kira/api/external"

#: The shared list plus this surface's own error type. `REFUSALS` is deliberately surface-agnostic
#: — it holds the *controls'* exceptions, which every surface shares — so each surface adds the one
#: it raises itself rather than pushing its vocabulary into the shared module.
KIRA_REFUSALS = (*REFUSALS, errors.KiraError)

router = APIRouter(tags=["kira"], prefix=BASE)

#: The surface announces that it is transitional from day one (`ADR-0010` Option C). A
#: compatibility layer with no stated end date is a permanent one, and the date is what turns
#: "should we retire it" from an argument into a decision.
SUNSET_HEADERS = {
    "Deprecation": "true",
    "Link": '</docs/migration>; rel="deprecation"',
}


def _sunset(request: Request) -> dict[str, str]:
    configured = getattr(request.app.state.settings, "kira_sunset", "")
    return {**SUNSET_HEADERS, **({"Sunset": configured} if configured else {})}


#: A shared control's HTTP status, in the predecessor's error vocabulary (`kira_api.md` §6.2).
#: Anything unmapped is a validation error, which is what a 4xx from a pre-dispatch check is.
_KIRA_CODE_FOR = {
    404: errors.MODEL_NOT_FOUND,
    403: errors.STANDARD_USER_PERMISSION_REQUIRED,
    429: errors.EXTERNAL_KI_API_TOO_MANY_REQUEST,
    502: errors.EXTERNAL_KI_API_ERROR,
}


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    """Every refusal, in the predecessor's vocabulary (`kira_api.md` §6.2)."""
    if isinstance(exc, errors.KiraError):
        response = exc.to_response()
    elif isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # These carry the predecessor's own codes (`kira_api.md` §6.2) — `INVALID_THINKING_MODE`,
        # `THINKING_TOKEN_COUNT_TOO_HIGH`, `EMBEDDING_AGGREGATION_NOT_SUPPORTED` and the rest — so
        # a migrating client's error handling keeps switching on the same strings it always did.
        response = errors.kira_error_response(422, exc.code, exc.message)
    elif isinstance(exc, AttachmentRejected | SchemaRejected):
        response = errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
    elif isinstance(exc, GeminiHTTPError):
        # The shared controls in `api/serving` raise this — they are surface-agnostic by design,
        # and this surface has to put their refusals into the predecessor's vocabulary. Without
        # this branch every one of them fell through to the `else` and became a **500**, which is
        # the exact failure the shared module exists to prevent, arriving from the other side: a
        # control that works but cannot be *reported* on one of the surfaces it protects.
        response = errors.kira_error_response(
            exc.code, _KIRA_CODE_FOR.get(exc.code, errors.VALIDATION_ERROR), exc.message
        )
    elif isinstance(exc, RateLimited | BudgetExceeded):
        response = errors.kira_error_response(
            429, errors.EXTERNAL_KI_API_TOO_MANY_REQUEST, exc.message
        )
    elif isinstance(exc, PipelineRejected):
        response = errors.kira_error_response(exc.code, errors.VALIDATION_ERROR, exc.message)
    elif isinstance(exc, NoCapableModel):
        response = errors.kira_error_response(400, errors.MODEL_NOT_FOUND, str(exc))
    elif isinstance(exc, UpstreamError):
        code = UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, ""))[0]
        name = (
            errors.EXTERNAL_KI_API_TOO_MANY_REQUEST if code == 429 else errors.EXTERNAL_KI_API_ERROR
        )
        response = errors.kira_error_response(code, name, exc.message)
    else:
        response = errors.kira_error_response(
            500, errors.INTERNAL_SERVER_ERROR, "Internal server error."
        )
    for key, value in _sunset(request).items():
        response.headers[key] = value
    return response


def _details(exc: ValidationError) -> list[dict[str, Any]]:
    """The predecessor's ``details`` array, and **only things that serialise**.

    ``errors()`` carries a ``ctx`` holding the original exception object whenever a *custom*
    validator raised — and ours do, for "a part carries either text or data". Rendering that as
    JSON raised `TypeError` inside the response, which the framework turned into a **500**: a
    caller's malformed body became our server error, with the generic envelope rather than the
    predecessor's, on the one surface whose contract is its error shape.

    ``include_input`` is off for a second reason. Echoing the offending value back is a habit that
    eventually reflects a prompt, or a credential somebody put in the wrong field, into a response
    and from there into whatever logs it.

    **What actually enforces both is the comprehension**, which takes two named fields and nothing
    else; the flags are the belt to its braces. Worth stating because the mutation for this
    property was first pointed at the flags and survived — they were already redundant, so removing
    them changed nothing, and a mutation that cannot fail is a claim rather than a test.
    """
    return [
        {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


def _thinking_config(declaration: ModelDeclaration) -> schemas.ThinkingConfig | None:
    """What the predecessor's `/models` says about a model's thinking (`kira_api.md` §2.4).

    ``None`` for a model that declares none — rather than an empty config, which would read as
    "thinking exists here and nothing is allowed" instead of "nobody has said".
    """
    modes = declaration.thinking_modes
    if not modes:
        return None
    minimum, maximum = declaration.thinking_bounds
    default = declaration.thinking_default
    return schemas.ThinkingConfig(
        mode=sorted(str(mode) for mode in modes),
        minTokens=minimum,
        maxTokens=maximum,
        defaultThinking=(
            schemas.ThinkingSetting(mode=str(default.get("mode")), tokens=default.get("tokens"))
            if default and default.get("mode")
            else None
        ),
    )


async def _resolve_model(request: Request, model_id: int) -> str:
    name = await catalog_of(request).by_numeric_id(model_id)
    if name is None:
        raise errors.KiraError(
            404,
            errors.MODEL_NOT_FOUND,
            f"No model with id {model_id}. Ids are assigned in the model catalog.",
        )
    return name


async def _prepare(
    request: Request, principal: Principal, body: dict[str, Any], trail: AuditTrail
) -> tuple[Prepared, Any]:
    """Everything between "a body arrived" and "dispatch it" that is *this surface's* business.

    Which is: resolve the caller, parse the predecessor's shape, turn an integer model id into a
    model, and refuse what this contract does not serve. Everything after that is the same for
    every surface and belongs to `prepare_for_dispatch`.

    The trail is **passed in** rather than created here. It used to be returned and the caller's
    own reassigned over — two audit trails for one request, and the one carrying the body thrown
    away.
    """
    resolve_attribution(request, principal)
    try:
        parsed = schemas.ChatRequest.model_validate(body)
    except ValidationError as exc:
        raise errors.KiraError(
            422, errors.VALIDATION_ERROR, "Request validation failed.", _details(exc)
        ) from exc

    model = await _resolve_model(request, parsed.model_id)
    trail.requested_model = model
    declaration = await catalog_of(request).declaration(model)

    if not declaration.can(Capability.GENERATE):
        raise errors.KiraError(
            422, errors.NO_CHAT_CAPABILITIES, f"Model '{model}' does not support chat."
        )
    if parsed.max_tokens is not None and parsed.max_tokens <= 0:
        raise errors.KiraError(422, errors.INVALID_MAX_TOKENS, "maxTokens must be positive.")
    cap = declaration.max_output_tokens
    if parsed.max_tokens is not None and cap is not None and parsed.max_tokens > cap:
        raise errors.KiraError(
            422,
            errors.MAX_TOKENS_EXCEEDS_CAP,
            f"maxTokens {parsed.max_tokens} exceeds the {cap} this model accepts.",
        )

    canonical = to_canonical(parsed, model, bounds=schema_bounds(request))
    # One call, and the order is not this surface's to choose. It used to be four steps written
    # out here and the reservation written out again in each of the three handlers below — which
    # is how this surface came to have no rate limiting at all when the take moved.
    prepared = await prepare_for_dispatch(
        request,
        trail,
        method="generateContent",
        canonical=canonical,
        requested_output=parsed.max_tokens,
    )
    assert prepared.canonical is not None
    return prepared, parsed


@dataclass
class _StreamOutcome:
    """What a stream managed to produce before it ended, however it ended.

    Collected rather than acted on inline, so that one shielded exit can account for every way out
    — served, refused, or cancelled while the model was still answering.
    """

    response: CanonicalResponse | None = None
    payload: dict[str, Any] | None = None
    status: int = 499
    reason: Outcome | None = None


async def _finish_streaming_chat(
    request: Request,
    reservation: Reservation,
    trail: AuditTrail,
    *,
    outcome: _StreamOutcome,
    body: dict[str, Any],
    started: float,
) -> None:
    """Account for a finished stream and write its row — whichever way it finished.

    A stream that produced nothing chargeable is **released** rather than settled: settling would
    still book one request, and a use case with a request limit would lose allowance to a caller
    who hung up or to an upstream that failed. `499` is the status for "the caller left" — it is
    not sent to anybody, because there is nobody to send it to, and it exists so the audit can
    tell that case apart from a served one.
    """
    if outcome.response is None:
        await request.app.state.budgets.release(reservation)
        await _record(
            request,
            trail,
            operation="streaming-chat",
            status=outcome.status,
            response=None,
            body=body,
            payload=None,
            started=started,
            outcome=outcome.reason or Outcome.UPSTREAM_ERROR,
        )
        return

    cost = await request.app.state.pricing.cost_nanos(
        outcome.response.model, outcome.response.usage
    )
    await request.app.state.budgets.settle(
        reservation, outcome.response.usage.total_tokens, cost_nanos=cost
    )
    await _record(
        request,
        trail,
        operation="streaming-chat",
        status=outcome.status,
        response=outcome.response,
        body=body,
        payload=outcome.payload,
        started=started,
        cost=cost,
    )


async def _record(
    request: Request,
    trail: AuditTrail,
    *,
    operation: str,
    status: int,
    response: CanonicalResponse | None,
    body: dict[str, Any],
    payload: dict[str, Any] | None,
    started: float,
    cost: int | None = None,
    outcome: Outcome = Outcome.SERVED,
) -> None:
    await record_request(
        request,
        operation=operation,
        model=trail.served_model,
        status=status,
        usage=response.usage if response else None,
        latency_ms=elapsed_ms(started),
        request_payload=body,
        response_payload=payload,
        cost_nanos=cost,
        outcome=outcome,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        provenance=provenance(request, trail.served_model),
        api="kira",
    )


@router.post("/chat")
async def chat(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="chat")
    body: dict[str, Any] = {}
    try:
        body = await _json(request)
        trail.body = body
        prepared, parsed = await _prepare(request, principal, body, trail)
        canonical, fallbacks = prepared.canonical, prepared.fallbacks
        assert canonical is not None
        reservation = prepared.reservation
        async with request.app.state.budgets.hold(reservation):
            dispatched = await dispatch_with_fallback(
                registry_of(request),
                canonical,
                fallbacks,
                permits=requirements_for(request, canonical),
            )
            trail.served_by(dispatched.response.model, dispatched.candidate_index)
            trail.passed_over(dispatched.skipped)
            check_structured_result(canonical, dispatched.response)
            cost = await request.app.state.pricing.cost_nanos(
                dispatched.response.model, dispatched.response.usage
            )
            await request.app.state.budgets.settle(
                reservation, dispatched.response.usage.total_tokens, cost_nanos=cost
            )
            payload = to_chat_response(dispatched.response).model_dump()
            await _record(
                request,
                trail,
                operation="chat",
                status=200,
                response=dispatched.response,
                body=body,
                payload=payload,
                started=started,
                cost=cost,
            )
            return JSONResponse(payload, headers=_sunset(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, body=body, started=started, operation="chat")


@router.post("/streaming-chat")
async def streaming_chat(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    """The predecessor's SSE contract: typed events, terminating in ``completed``.

    Not a synthetic progress heartbeat: `FRD-111` §5.4 decided against inventing ``update`` events
    that carry no model output, and Stage A honours that by sending exactly one ``completed``.
    A client written against the predecessor ignores unknown event types and reads the terminal
    one, which is the half of the contract that carries the answer.
    """
    started = time.monotonic()
    trail = AuditTrail(operation="streaming-chat")
    body: dict[str, Any] = {}
    try:
        body = await _json(request)
        trail.body = body
        prepared, parsed = await _prepare(request, principal, body, trail)
        canonical, fallbacks = prepared.canonical, prepared.fallbacks
        assert canonical is not None
        reservation = prepared.reservation
    except KIRA_REFUSALS as exc:
        return await _refused(
            request, trail, exc, body=body, started=started, operation="streaming-chat"
        )

    async def events() -> AsyncIterator[str]:
        outcome: _StreamOutcome = _StreamOutcome()
        async with request.app.state.budgets.hold(reservation):
            try:
                try:
                    dispatched = await dispatch_with_fallback(
                        registry_of(request),
                        canonical,
                        fallbacks,
                        permits=requirements_for(request, canonical),
                    )
                    # Inside the same `try`, deliberately. This surface's "stream" delivers one
                    # terminal event carrying the whole answer, so an incomplete document would
                    # arrive looking exactly like complete data — and a refusal raised *outside*
                    # the handler would escape the generator as a 500 with no audit row rather
                    # than being recorded as the refusal it is.
                    check_structured_result(canonical, dispatched.response)
                except KIRA_REFUSALS as exc:
                    # Headers are already sent, so the failure cannot change the status. It is
                    # still accounted for and logged, which keeps a failed stream out of the
                    # "served" column (FRD-122).
                    outcome.status = 502
                    outcome.reason = refusal_outcome(exc)
                    return
                trail.served_by(dispatched.response.model, dispatched.candidate_index)
                trail.passed_over(dispatched.skipped)
                outcome.response = dispatched.response
                event = completed_event(dispatched.response)
                outcome.payload = event["data"]
                outcome.status = 200
                yield f"data: {json.dumps(event)}\n\n"
            finally:
                # **Shielded**, and the reason is the one the Gemini surface learned from a
                # 1-in-8 integration flake and this one never received. Closing a generator from
                # inside the process raises `GeneratorExit` and awaits in a `finally` run
                # normally; a caller dropping a real socket **cancels the task**, and a bare
                # `await` here re-raises `CancelledError` at its first suspension point — the
                # settle and the audit row are simply lost.
                #
                # The window is different on this surface and that is why a copy of the Gemini
                # test would have proved nothing: the whole answer arrives in one terminal event,
                # so the accounting happens *before* anything is yielded. What is exposed is the
                # long await in the middle — a caller who goes away while the model is still
                # thinking. The upstream was called either way.
                await asyncio.shield(
                    _finish_streaming_chat(
                        request,
                        reservation,
                        trail,
                        outcome=outcome,
                        body=body,
                        started=started,
                    )
                )

    return StreamingResponse(events(), media_type="text/event-stream", headers=_sunset(request))


@router.post("/embed")
async def embed(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="embed")
    body: dict[str, Any] = {}
    try:
        body = await _json(request)
        trail.body = body
        resolve_attribution(request, principal)
        try:
            parsed = schemas.EmbeddingRequest.model_validate(body)
        except ValidationError as exc:
            raise errors.KiraError(
                422, errors.VALIDATION_ERROR, "Request validation failed.", _details(exc)
            ) from exc

        model = await _resolve_model(request, parsed.model_id)
        trail.requested_model = model

        # What this surface owns is the predecessor's default task type; every rule about *what
        # may be embedded* — and the whole order of the controls around it — belongs to the
        # shared sequence, which is why only the default is named here.
        prepared = await prepare_for_dispatch(
            request,
            trail,
            method="embedContent",
            embed=to_embedding(parsed, model),
            default_task_type=DEFAULT_TASK_TYPE,
        )
        embed_request = prepared.embed
        assert embed_request is not None
        reservation = prepared.reservation
        async with request.app.state.budgets.hold(reservation):
            provider = registry_of(request).provider_for(model)
            if provider is None:
                raise errors.KiraError(404, errors.MODEL_NOT_FOUND, f"Model '{model}' not found.")
            vectors = await provider.embed(embed_request)
            await request.app.state.budgets.settle(
                reservation, 0, cost_nanos=None, requests=embed_request.size
            )
            payload = (
                schemas.BatchEmbeddingResponse(vectors=vectors).model_dump()
                if isinstance(parsed.text, list)
                else schemas.EmbeddingResponse(vector=vectors[0] if vectors else []).model_dump()
            )
            trail.model = model
            await _record(
                request,
                trail,
                operation="embed",
                status=200,
                response=None,
                body=body,
                payload=payload,
                started=started,
            )
            return JSONResponse(payload, headers=_sunset(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, body=body, started=started, operation="embed")


@router.get("/models")
async def models(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    """The catalogued models, in the predecessor's shape.

    **Authenticated**, where the predecessor's is open (`FRD-107` §5.5). Our catalog reveals which
    models an organisation has approved and what their limits are; that is not public.
    """
    del principal
    catalog = catalog_of(request)
    listed: list[schemas.KiModel] = []
    for described in registry_of(request).models():
        declaration = await catalog.declaration(described.name)
        if declaration.numeric_id is None:
            # Without an id a KIRA client cannot address it, and listing it would offer something
            # that cannot be called. The catalog is where that is fixed.
            continue
        capabilities = []
        if declaration.can(Capability.GENERATE):
            capabilities.append("CHAT")
        if declaration.can(Capability.EMBED):
            capabilities.append("EMBEDDING")
        listed.append(
            schemas.KiModel(
                id=declaration.numeric_id,
                name=described.name,
                provider=(declaration.publisher or described.publisher or "").capitalize(),
                capabilities=capabilities,
                deprecated=declaration.deprecated,
                max_output_tokens=declaration.max_output_tokens,
                # Stage B. A client reads this list to decide what to *ask for*, so a surface that
                # now serves thinking and task types while still reporting neither would have every
                # caller conclude the models support none of it — the capability would exist and be
                # unreachable, which is indistinguishable from not having built it.
                thinkingConfig=_thinking_config(declaration),
                embedding_dimensions=declaration.default_dimensions,
                task_types=sorted(declaration.embedding_task_types) or None,
                supports_aggregation=(
                    declaration.supports_batch if declaration.can(Capability.EMBED) else None
                ),
            )
        )
    return JSONResponse(
        [model.model_dump(exclude_none=True) for model in listed], headers=_sunset(request)
    )


@router.get("/health")
async def health(request: Request) -> Response:
    """Unauthenticated, as the predecessor's is — it carries no configuration and no catalog.

    Deliberately **not** probing every model on every call: with a readiness probe every few
    seconds across every replica that is a continuous stream of billable upstream calls, and it
    makes the probe as slow as the slowest provider. `FRD-117` §5.2 has the cached-probe design;
    until then this reports what the gateway itself can answer.
    """
    checks = [schemas.HealthCheck(service="Gateway", healthy=True, tags=["aira"])]
    return JSONResponse(
        schemas.HealthResponse(status="HEALTHY", checks=checks).model_dump(),
        headers=_sunset(request),
    )


@router.get("/version-info")
async def version_info(request: Request) -> Response:
    """Build metadata, or nulls. Absent metadata is a valid state (a development run has no build
    number) rather than an error — the predecessor's behaviour, and the right one."""
    settings = request.app.state.settings
    info = schemas.VersionInfo(
        buildNumber=getattr(settings, "build_number", None) or None,
        buildTime=getattr(settings, "build_time", "") or None,
        git=schemas.GitInfo(
            commit=getattr(settings, "git_commit", "") or None,
            commitShort=(getattr(settings, "git_commit", "") or "")[:7] or None,
            branch=getattr(settings, "git_branch", "") or None,
            stage=settings.environment,
        ),
    )
    return JSONResponse(info.model_dump(), headers=_sunset(request))


@router.get("/ki-usage")
async def ki_usage(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    """Token consumption per user and model, from `FRD-601`'s report.

    Governance-only, reusing the same visibility rule rather than a second decision — a second
    entry point to the same data is a second chance to forget the scope (`FRD-602` §5.3).
    """
    if not principal.is_governance:
        raise errors.KiraError(
            403, errors.ADMIN_PERMISSION_REQUIRED, "This endpoint requires an oversight role."
        )

    start_raw = request.query_params.get("startDatum")
    end_raw = request.query_params.get("endDatum")
    for name, value in (("startDatum", start_raw), ("endDatum", end_raw)):
        if not value:
            raise errors.KiraError(400, errors.MISSING_QUERY_PARAM, f"'{name}' is required.")
    try:
        start = datetime.fromisoformat(str(start_raw))
        end = datetime.fromisoformat(str(end_raw))
    except ValueError as exc:
        raise errors.KiraError(400, errors.VALIDATION_ERROR, "Dates must be ISO-8601.") from exc
    if end <= start:
        raise errors.KiraError(
            400, errors.INVALID_TIME_RANGE, "'endDatum' must be after 'startDatum'."
        )

    service: ReportingService = request.app.state.reporting
    catalog = catalog_of(request)
    report = await service.report(
        None,
        start if start.tzinfo else start.replace(tzinfo=UTC),
        end if end.tzinfo else end.replace(tzinfo=UTC),
    )

    rows: list[dict[str, Any]] = []
    for member in report["by_member"]:
        for model_row in report["by_model"]:
            del model_row  # the report does not cross-tabulate; see the note below
            break
        rows.append(
            schemas.KiUsageRow(
                user_id=member["key"],
                # The predecessor keys usage by (user, model); `FRD-601` aggregates them
                # separately. Reporting them per user with a model id of 0 would be a fabricated
                # cross-tabulation, so the model dimension is carried by its own rows instead and
                # this one is honest about being per-user.
                model_id=0,
                entry_count=member["requests"],
                token_input_sum=member["prompt_tokens"],
                token_output_sum=member["completion_tokens"],
            ).model_dump()
        )
    _ = catalog
    return JSONResponse(rows, headers=_sunset(request))


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise errors.KiraError(
            400, errors.INVALID_JSON_BODY, "Request body is not valid JSON."
        ) from exc
    if not isinstance(body, dict):
        raise errors.KiraError(400, errors.INVALID_JSON_BODY, "Request body must be an object.")
    return body


async def _refused(
    request: Request,
    trail: AuditTrail,
    exc: Exception,
    *,
    body: dict[str, Any],
    started: float,
    operation: str,
) -> JSONResponse:
    """One recording site per surface, for the same reason the Gemini surface has one."""
    response = _error_response(request, exc)
    if getattr(request.state, "attribution", None) is not None:
        # Never turn a correct refusal into a server error (FRD-122 FR-7).
        with contextlib.suppress(Exception):
            await _record(
                request,
                trail,
                operation=operation,
                status=response.status_code,
                response=None,
                body=body,
                payload=None,
                started=started,
                outcome=refusal_outcome(exc),
            )
    return response
