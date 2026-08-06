"""The predecessor's endpoints, served by AIRA (FRD-107 Stage A).

Under ``/kira/api/external``, so a migrating consumer changes a base URL and nothing else. This
module maps one wire format; **everything below the surface is shared** with the Gemini routes via
``api/serving`` — the pre-dispatch gate, the pipeline, the dispatch chain, the audit writer. A
second copy of those controls is the `:embedContent` failure with an extra hundred lines to hide in.

Stage A serves text **and documents** (`FRD-110` landed first), and **refuses** thinking and
`responseSchema` by name. Refusing is the contract: a field accepted and ignored produces an answer
that is wrong for a reason the caller cannot see.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from aira_common.models import Capability
from aira_gateway.api.kira import errors, schemas
from aira_gateway.api.kira.attribution import resolve as resolve_attribution
from aira_gateway.api.kira.mapping import (
    completed_event,
    refuse_unsupported,
    to_canonical,
    to_chat_response,
)
from aira_gateway.api.serving import (
    REFUSALS,
    UPSTREAM_STATUS_MAP,
    catalog_of,
    check_declaration,
    elapsed_ms,
    enforce_pre_dispatch,
    provenance,
    refusal_outcome,
    registry_of,
    requirements_for,
    run_pipeline,
)
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.reporting.service import ReportingService
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


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    """Every refusal, in the predecessor's vocabulary (`kira_api.md` §6.2)."""
    if isinstance(exc, errors.KiraError):
        response = exc.to_response()
    elif isinstance(exc, AttachmentRejected):
        response = errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
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
    request: Request, principal: Principal, body: dict[str, Any], *, operation: str
) -> tuple[CanonicalRequest, tuple[str, ...], AuditTrail, Any]:
    """Everything between "a body arrived" and "dispatch it"."""
    resolve_attribution(request, principal)
    try:
        parsed = schemas.ChatRequest.model_validate(body)
    except ValidationError as exc:
        raise errors.KiraError(
            422,
            errors.VALIDATION_ERROR,
            "Request validation failed.",
            exc.errors(include_url=False),
        ) from exc

    model = await _resolve_model(request, parsed.model_id)
    trail = AuditTrail(operation=operation, requested_model=model)
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

    refuse_unsupported(parsed, declaration)

    canonical = to_canonical(parsed, model)
    canonical, fallbacks = await run_pipeline(request, canonical, trail)
    await check_declaration(
        request, model=canonical.model, method="generateContent", requested=parsed.max_tokens
    )
    return canonical, fallbacks, trail, parsed


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
        canonical, fallbacks, trail, parsed = await _prepare(
            request, principal, body, operation="chat"
        )
        reservation = await enforce_pre_dispatch(
            request,
            model=canonical.model,
            max_output_tokens=canonical.max_output_tokens,
            attachments=[part.media_type for part in canonical.attachments],
        )
        async with request.app.state.budgets.hold(reservation):
            dispatched = await dispatch_with_fallback(
                registry_of(request),
                canonical,
                fallbacks,
                permits=requirements_for(request, canonical),
            )
            trail.served_by(dispatched.response.model, dispatched.candidate_index)
            trail.passed_over(dispatched.skipped)
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
        canonical, fallbacks, trail, parsed = await _prepare(
            request, principal, body, operation="streaming-chat"
        )
        reservation = await enforce_pre_dispatch(
            request,
            model=canonical.model,
            max_output_tokens=canonical.max_output_tokens,
            attachments=[part.media_type for part in canonical.attachments],
        )
    except KIRA_REFUSALS as exc:
        return await _refused(
            request, trail, exc, body=body, started=started, operation="streaming-chat"
        )

    async def events() -> AsyncIterator[str]:
        async with request.app.state.budgets.hold(reservation):
            try:
                dispatched = await dispatch_with_fallback(
                    registry_of(request),
                    canonical,
                    fallbacks,
                    permits=requirements_for(request, canonical),
                )
            except KIRA_REFUSALS as exc:
                # Headers are already sent, so the failure cannot change the status. It is still
                # accounted for and logged, which is what keeps a failed stream out of the "served"
                # column (FRD-122).
                await _record(
                    request,
                    trail,
                    operation="streaming-chat",
                    status=502,
                    response=None,
                    body=body,
                    payload=None,
                    started=started,
                    outcome=refusal_outcome(exc),
                )
                return
            trail.served_by(dispatched.response.model, dispatched.candidate_index)
            trail.passed_over(dispatched.skipped)
            cost = await request.app.state.pricing.cost_nanos(
                dispatched.response.model, dispatched.response.usage
            )
            await request.app.state.budgets.settle(
                reservation, dispatched.response.usage.total_tokens, cost_nanos=cost
            )
            event = completed_event(dispatched.response)
            await _record(
                request,
                trail,
                operation="streaming-chat",
                status=200,
                response=dispatched.response,
                body=body,
                payload=event["data"],
                started=started,
                cost=cost,
            )
            yield f"data: {json.dumps(event)}\n\n"

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
                422,
                errors.VALIDATION_ERROR,
                "Request validation failed.",
                exc.errors(include_url=False),
            ) from exc

        texts = [parsed.text] if isinstance(parsed.text, str) else list(parsed.text)
        if not texts or any(not text.strip() for text in texts):
            raise errors.KiraError(
                422, errors.VALIDATION_ERROR, "text must be a non-empty string or list of them."
            )
        if len(texts) > 1:
            # `FRD-113` brings batching, task types and the metering that stops a batch being a
            # way around a rate limit. Refused until then rather than embedded one at a time,
            # which would silently cost N requests' worth of quota against a limit of one.
            raise errors.KiraError(
                422,
                errors.NOT_YET_SUPPORTED,
                "List embedding is not yet available on this gateway.",
            )
        if parsed.task_type is not None:
            raise errors.KiraError(
                422,
                errors.NOT_YET_SUPPORTED,
                "'task_type' is not yet available on this gateway. It is refused rather than "
                "ignored: the wrong optimisation type produces vectors that retrieve measurably "
                "worse, and nothing about the response would show it.",
            )

        model = await _resolve_model(request, parsed.model_id)
        trail.requested_model = model
        declaration = await catalog_of(request).declaration(model)
        if not declaration.can(Capability.EMBED):
            raise errors.KiraError(
                422,
                errors.NO_EMBEDDING_CAPABILITIES,
                f"Model '{model}' does not support embedding.",
            )

        reservation = await enforce_pre_dispatch(request, model=model, max_output_tokens=None)
        async with request.app.state.budgets.hold(reservation):
            provider = registry_of(request).provider_for(model)
            if provider is None:
                raise errors.KiraError(404, errors.MODEL_NOT_FOUND, f"Model '{model}' not found.")
            vector = await provider.embed(model, texts[0])
            await request.app.state.budgets.settle(reservation, 0, cost_nanos=None)
            payload = schemas.EmbeddingResponse(vector=vector).model_dump()
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
