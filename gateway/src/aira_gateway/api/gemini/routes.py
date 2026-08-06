"""Gemini-compatible routes (FRD-100).

Mirrors Google's colon-verb convention: ``POST /v1beta/models/{model}:{method}``. Requests
are validated against the Gemini schema, mapped to canonical, dispatched to the resolved
provider, and mapped back. Errors use the Gemini error envelope.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from aira_common.logging import get_logger
from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_common.observability import set_span_attributes
from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    gemini_to_canonical,
    upstream_model_to_gemini,
)
from aira_gateway.audit import AuditTrail, Outcome, decision_summary
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest, CanonicalUsage
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, Permits, dispatch_with_fallback
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.requirements import RegionAllowed, permits
from aira_gateway.upstreams.base import ProviderRegistry, Upstream, UpstreamError

_log = get_logger("aira_gateway")


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


router = APIRouter(tags=["gemini"])


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}".strip(": ")


# Pass meaningful upstream statuses (rate limit / unavailable / timeout) through to the client;
# everything else — upstream 4xx caused by *our* key/config, upstream 5xx, or a transport failure
# (status_code is None) — is surfaced as a generic 502, so a broken upstream is never mistaken for a
# client mistake.
_UPSTREAM_STATUS_MAP: dict[int, tuple[int, str]] = {
    429: (429, "RESOURCE_EXHAUSTED"),
    503: (503, "UNAVAILABLE"),
    504: (504, "DEADLINE_EXCEEDED"),
}


async def _enforce_pre_dispatch(
    request: Request, *, model: str, max_output_tokens: int | None
) -> Reservation:
    """Every control a request must clear before anything expensive happens.

    Rate limiting comes first: the whole point of a limit is that the upstream call never
    happens. The budget then reserves what the request is expected to consume, so requests in
    flight are visible to each other's check instead of all passing the same stale figure.
    """
    attribution = getattr(request.state, "attribution", None)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)

    await request.app.state.rate_limits.check(use_case, subject)
    estimate = await _estimate(request, model=model, max_output_tokens=max_output_tokens)
    # `app.state` is untyped, so the annotation is what states the contract the route relies on.
    reservation: Reservation = await request.app.state.budgets.guard(
        use_case, subject, estimated=estimate
    )
    return reservation


async def _estimate(request: Request, *, model: str, max_output_tokens: int | None) -> Amounts:
    """What this request is expected to consume, for the pre-dispatch reservation (FRD-405).

    The real cost is unknowable before the model answers, so the estimate is deliberately
    conservative: the caller's own ``maxOutputTokens`` where it bounded the response, a
    configured default otherwise, and priced entirely at the **output** rate, which every
    provider charges several times higher than input. Over-reserving briefly is the safe
    direction for a spend limit, and the figure is corrected the moment the response arrives.

    An unpriced model estimates zero cost — the same "unknown is not zero" rule as everywhere
    else: it is not counted as free, it simply cannot constrain a cost limit.
    """
    settings = request.app.state.settings
    # The model's own default before the installation-wide one: a per-model figure is a better
    # estimate for every vendor, and it is the same number the request will actually carry.
    declaration = await _catalog(request).declaration(model)
    tokens = declaration.output_cap(max_output_tokens) or settings.budget_estimate_output_tokens
    price = await request.app.state.pricing.price_for(model)
    cost = 0 if price is None else cost_nanos(tokens, price.output_per_million_nanos)
    return Amounts(tokens=tokens, requests=1, cost_nanos=cost)


def _upstream_error(exc: UpstreamError) -> JSONResponse:
    code, status = _UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, "UNAVAILABLE"))
    return _error(code, exc.message, status)


def _registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


def _provenance(request: Request, model: str) -> tuple[str, str, str] | None:
    """Where the request was processed, from the adapter that serves the model.

    Read from the registry rather than the catalog: the catalog says where a model is *configured*
    to run, the registry says which adapter actually holds it. Under a residency requirement the
    second is the one worth recording.
    """
    described = _registry(request).get_model(model)
    if described is None or not described.provider:
        return None
    return (described.provider, described.publisher, described.region)


def _requirements(request: Request) -> Permits:
    """What a candidate must satisfy to serve this request (`ADR-0012` §3).

    Assembled per request because the answer depends on the request: today only residency, and
    `FRD-110` adds the attachment media types the caller actually sent.
    """
    settings = request.app.state.settings
    allowed = tuple(
        region.strip() for region in settings.vertex_allowed_regions.split(",") if region.strip()
    )
    return permits([RegionAllowed(_registry(request), allowed)])


def _catalog(request: Request) -> ModelCatalog:
    catalog: ModelCatalog = request.app.state.catalog
    return catalog


async def _check_declaration(
    request: Request, *, model: str, method: str, requested: int | None
) -> ModelDeclaration:
    """Every rule the catalog decides, before anything expensive happens (FRD-114).

    Returns the declaration, so the caller can act on ``deprecated`` without a second lookup.
    """
    declaration = await _catalog(request).declaration(model)

    if method == "embedContent" and not declaration.can(Capability.EMBED):
        # Refused *before* dispatch rather than by an adapter raising deep in the stack: with
        # cross-vendor routing (ADR-0012) a chain can send an embedding to a model that has no
        # embedding endpoint at all, and the useful error names the model (FRD-113 FR-6a).
        raise GeminiHTTPError(
            400,
            f"Model '{model}' does not support embedding.",
            "INVALID_ARGUMENT",
        )
    if method != "embedContent" and not declaration.can(Capability.GENERATE):
        raise GeminiHTTPError(
            400, f"Model '{model}' does not support generation.", "INVALID_ARGUMENT"
        )

    cap = declaration.max_output_tokens
    if requested is not None and cap is not None and requested > cap:
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens {requested} exceeds the {cap} this model accepts.",
            "INVALID_ARGUMENT",
        )
    return declaration


def _deprecation_headers(declaration: ModelDeclaration) -> dict[str, str]:
    """A ``Warning`` header for a deprecated model (FRD-114 FR-5).

    It **warns, it does not block**. Blocking is what `FRD-307`'s revocation is for, and conflating
    the two removes the ability to announce a retirement before performing one — which is the whole
    point of having a deprecation flag rather than just deleting the row.
    """
    if not declaration.deprecated:
        return {}
    return {
        "Warning": f'299 - "Model {declaration.name} is deprecated and will be withdrawn."',
    }


async def _run_pipeline(
    request: Request, canonical: CanonicalRequest, trail: AuditTrail
) -> tuple[CanonicalRequest, tuple[str, ...]]:
    """Apply the use case's pre-dispatch pipeline (FRD-300). Pass-through when none is configured.

    Returns the effective request (possibly re-routed) and the dispatch fallback chain. May raise
    ``PipelineRejected`` when a filter/allow-check blocks the request — and the decisions taken up
    to that point are on the trail by then, so a blocked request records *why* rather than only
    *that* (FRD-122 FR-4).
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return canonical, ()
    # The engine appends into the trail's list, so a step that blocks still leaves behind the
    # decisions taken before it — including the routing that sent the request to the step that
    # refused it.
    outcome = await engine.run(pipeline, canonical, decisions=trail.decisions)
    trail.routed_to(outcome.request.model)
    if outcome.decisions:
        set_span_attributes(
            {
                "aira.pipeline.decisions": len(outcome.decisions),
                "aira.pipeline.model": outcome.request.model,
            }
        )
        _log.info(
            "pipeline_applied",
            use_case=use_case,
            model=outcome.request.model,
            decisions=outcome.decisions,
        )
    return outcome.request, outcome.fallback_models


async def _described(request: Request, model: schemas.GeminiModel) -> schemas.GeminiModel:
    """Attach what the catalog declares, so the list says what each model may be asked to do."""
    declaration = await _catalog(request).declaration(model.name.removeprefix("models/"))
    return model.model_copy(
        update={
            "airaCapabilities": sorted(str(c) for c in declaration.capabilities),
            "airaMaxOutputTokens": declaration.max_output_tokens,
            "airaDeprecated": declaration.deprecated,
            # Surfaced the same way an unpriced model is: visibly incomplete rather than absent,
            # because an undeclared model silently does less than the list suggests.
            "airaDeclared": declaration.declared,
        }
    )


@router.get("/v1beta/models")
async def list_models(request: Request) -> JSONResponse:
    models = [
        await _described(request, upstream_model_to_gemini(m)) for m in _registry(request).models()
    ]
    return JSONResponse(schemas.ListModelsResponse(models=models).model_dump())


@router.get("/v1beta/models/{model}")
async def get_model(model: str, request: Request) -> Response:
    upstream_model = _registry(request).get_model(model)
    if upstream_model is None:
        return _error(404, f"Model '{model}' not found.", "NOT_FOUND")
    described = await _described(request, upstream_model_to_gemini(upstream_model))
    return JSONResponse(described.model_dump())


#: How a refusal maps onto the closed outcome vocabulary. Anything unmapped is a bug in this
#: table, not a reason to record nothing — see ``_refusal_outcome``.
_REFUSAL_OUTCOMES: dict[int, Outcome] = {
    404: Outcome.MODEL_NOT_FOUND,
    400: Outcome.INVALID_REQUEST,
    403: Outcome.BLOCKED_BY_PIPELINE,
}


def _refusal_outcome(exc: Exception) -> Outcome:
    if isinstance(exc, NoCapableModel):
        return Outcome.NO_CAPABLE_MODEL
    if isinstance(exc, RateLimited):
        return Outcome.RATE_LIMITED
    if isinstance(exc, BudgetExceeded):
        return Outcome.BUDGET_EXCEEDED
    if isinstance(exc, PipelineRejected):
        return Outcome.BLOCKED_BY_PIPELINE
    if isinstance(exc, UpstreamError):
        return Outcome.UPSTREAM_ERROR
    if isinstance(exc, GeminiHTTPError):
        return _REFUSAL_OUTCOMES.get(exc.code, Outcome.INVALID_REQUEST)
    return Outcome.INVALID_REQUEST


def _refusal_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, NoCapableModel):
        # A 400, not the 502 this used to be. "Every candidate was excluded" is a configuration or
        # capability problem somebody can fix; an upstream outage is not, and reporting them as the
        # same status sends whoever reads it to the wrong place.
        return _error(400, str(exc), "FAILED_PRECONDITION")
    if isinstance(exc, RateLimited):
        return _error(
            429, exc.message, "RESOURCE_EXHAUSTED", headers={"Retry-After": exc.retry_after}
        )
    if isinstance(exc, BudgetExceeded):
        return _error(429, exc.message, "RESOURCE_EXHAUSTED")
    if isinstance(exc, PipelineRejected):
        return _error(exc.code, exc.message, exc.status)
    if isinstance(exc, UpstreamError):
        return _upstream_error(exc)
    assert isinstance(exc, GeminiHTTPError)
    return exc.to_response()


@router.post("/v1beta/models/{resource}")
async def generate(resource: str, request: Request) -> Response:
    """Dispatch a Gemini verb — and record the request whether or not it was served.

    Every refusal is written **here**, once. The obvious alternative is a ``record_request`` beside
    each ``return _error(...)``; there are half a dozen of those, the next verb adds more, and one
    of them will be forgotten. That is not hypothetical — it is exactly how ``:embedContent`` came
    to bypass the pre-dispatch gate, because the gate lived inside one branch instead of on the
    path every branch takes. So the branches *raise* and the boundary records (FRD-122 §5.1).
    """
    model, _, method = resource.partition(":")
    trail = AuditTrail(operation=method or "unknown", requested_model=model)
    started = time.monotonic()
    try:
        return await _generate(resource, request, trail)
    except (
        RateLimited,
        BudgetExceeded,
        PipelineRejected,
        NoCapableModel,
        UpstreamError,
        GeminiHTTPError,
    ) as exc:
        response = _refusal_response(exc)
        await _record_refusal(request, trail, exc, status=response.status_code, started=started)
        return response


async def _record_refusal(
    request: Request, trail: AuditTrail, exc: Exception, *, status: int, started: float
) -> None:
    """Write the audit row for a request that was not served.

    Deliberately not conditional on anything: a refusal that leaves no trace is a control nobody
    can review, which is the whole reason `FRD-122` exists. The reason lives in ``outcome``; the
    error's own message stays in the response and the log, because a free-text reason on the row
    would be greppable and never groupable.

    A request refused before the auth dependency resolved an attribution has nothing to attribute
    and is not recorded here — a 401 is an authentication event, and writing a row per
    unauthenticated request would make the audit table a denial-of-service target (FRD-122 §2).
    """
    if getattr(request.state, "attribution", None) is None:
        return
    try:
        await _write_refusal(request, trail, exc, status=status, started=started)
    except Exception:  # noqa: BLE001 — see below
        # The audit must never become a way to fail a request that was **correctly refused**.
        # Turning a 429 into a 500 misinforms the client about what happened and invites the
        # retry storm the limit exists to prevent. The row is lost and said to be lost, loudly.
        #
        # Deliberately not applied to the success path: there, a failed write means a served
        # request went unrecorded, and failing loudly is the defensible answer to that.
        _log.error(
            "audit_refusal_not_recorded",
            operation=trail.operation,
            model=trail.served_model,
            status=status,
            outcome=str(_refusal_outcome(exc)),
            exc_info=True,
        )


async def _write_refusal(
    request: Request, trail: AuditTrail, exc: Exception, *, status: int, started: float
) -> None:
    await record_request(
        request,
        operation=trail.operation,
        model=trail.served_model,
        status=status,
        usage=None,
        latency_ms=_elapsed_ms(started),
        request_payload=trail.body,
        response_payload=None,
        outcome=_refusal_outcome(exc),
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        provenance=_provenance(request, trail.served_model),
    )


async def _generate(resource: str, request: Request, trail: AuditTrail) -> Response:
    model, separator, method = resource.partition(":")
    if not separator:
        raise GeminiHTTPError(
            400, f"Missing method in '{resource}' (expected model:method).", "INVALID_ARGUMENT"
        )

    provider = _registry(request).provider_for(model)
    if provider is None:
        raise GeminiHTTPError(404, f"Model '{model}' not found.", "NOT_FOUND")

    try:
        body = await request.json()
    except ValueError:
        raise GeminiHTTPError(400, "Request body is not valid JSON.", "INVALID_ARGUMENT") from None
    trail.body = body

    # Parse and prepare per method, then run the pre-dispatch controls once for all of them.
    canonical: CanonicalRequest | None = None
    fallbacks: tuple[str, ...] = ()
    embed_request: schemas.EmbedContentRequest | None = None

    if method in ("generateContent", "streamGenerateContent"):
        try:
            gemini_request = schemas.GenerateContentRequest.model_validate(body)
        except ValidationError as exc:
            raise GeminiHTTPError(400, _first_error(exc), "INVALID_ARGUMENT") from exc
        canonical = gemini_to_canonical(model, gemini_request)

        canonical, fallbacks = await _run_pipeline(request, canonical, trail)

        # Routing may have changed the model — resolve the effective provider.
        provider = _registry(request).provider_for(canonical.model)
        if provider is None:
            raise GeminiHTTPError(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")
    elif method == "embedContent":
        try:
            embed_request = schemas.EmbedContentRequest.model_validate(body)
        except ValidationError as exc:
            raise GeminiHTTPError(400, _first_error(exc), "INVALID_ARGUMENT") from exc
    else:
        raise GeminiHTTPError(400, f"Unknown method '{method}'.", "INVALID_ARGUMENT")

    # One gate for every method. Keeping these inside the generateContent branch is how
    # `:embedContent` ended up unlimited and unbudgeted — a caller only had to pick the other
    # verb. A control that applies to some verbs and not others has to be impossible to write
    # by accident, so there is exactly one place to add the next one.
    effective_model = canonical.model if canonical is not None else model
    requested_output = canonical.max_output_tokens if canonical is not None else None
    declaration = await _check_declaration(
        request, model=effective_model, method=method, requested=requested_output
    )
    reservation = await _enforce_pre_dispatch(
        request,
        model=effective_model,
        max_output_tokens=canonical.max_output_tokens if canonical is not None else None,
    )

    if canonical is not None:
        if method == "generateContent":
            # `hold` releases the reservation unless it is settled inside the block, so no exit
            # path — an upstream outage, a pricing lookup that fails, an outright bug — can leave
            # budget consumed by a request that produced nothing (FRD-405 FR-5).
            async with request.app.state.budgets.hold(reservation):
                started = time.monotonic()
                dispatched = await dispatch_with_fallback(
                    _registry(request),
                    canonical,
                    fallbacks,
                    permits=_requirements(request),
                )
                canonical_response = dispatched.response
                trail.served_by(canonical_response.model, dispatched.candidate_index)
                trail.passed_over(dispatched.skipped)
                # Priced once, then shared: the budget counters and the audit trail must not be
                # able to disagree about what a request cost (FRD-403).
                cost = await request.app.state.pricing.cost_nanos(
                    canonical_response.model, canonical_response.usage
                )
                await request.app.state.budgets.settle(
                    reservation, canonical_response.usage.total_tokens, cost_nanos=cost
                )
                payload = canonical_to_gemini(canonical_response).model_dump()
                await record_request(
                    request,
                    operation="generateContent",
                    model=canonical_response.model,
                    status=200,
                    usage=canonical_response.usage,
                    latency_ms=_elapsed_ms(started),
                    request_payload=body,
                    response_payload=payload,
                    cost_nanos=cost,
                    requested_model=trail.requested_model,
                    model_selection=trail.selection,
                    pipeline_decisions=decision_summary(trail.decisions),
                    provenance=_provenance(request, canonical_response.model),
                )
                return JSONResponse(payload, headers=_deprecation_headers(declaration))
        sse = request.query_params.get("alt") == "sse"
        return _stream_response(
            request,
            provider,
            canonical,
            body,
            reservation,
            trail,
            sse=sse,
            headers=_deprecation_headers(declaration),
        )

    assert embed_request is not None  # the method dispatch above guarantees it
    async with request.app.state.budgets.hold(reservation):
        started = time.monotonic()
        text = "".join(part.text for part in embed_request.content.parts)
        values = await provider.embed(model, text)
        payload = schemas.EmbedContentResponse(
            embedding=schemas.ContentEmbedding(values=values)
        ).model_dump()
        # Embeddings report no token usage, so there is nothing to price. The request still
        # counts against a request-limited budget, which is what settling with zero tokens does.
        await request.app.state.budgets.settle(reservation, 0, cost_nanos=None)
        await record_request(
            request,
            operation="embedContent",
            model=model,
            status=200,
            usage=None,
            latency_ms=_elapsed_ms(started),
            request_payload=body,
            response_payload=payload,
            requested_model=trail.requested_model,
            provenance=_provenance(request, model),
        )
        return JSONResponse(payload, headers=_deprecation_headers(declaration))


def _chunk_to_gemini(chunk: CanonicalChunk, model: str) -> schemas.GenerateContentResponse:
    usage = chunk.usage
    return schemas.GenerateContentResponse(
        candidates=[
            schemas.Candidate(
                content=schemas.Content(role="model", parts=[schemas.Part(text=chunk.text_delta)]),
                finishReason=(chunk.finish_reason.upper() if chunk.finish_reason else ""),
                index=0,
            )
        ],
        usageMetadata=schemas.UsageMetadata(
            promptTokenCount=usage.prompt_tokens if usage else 0,
            candidatesTokenCount=usage.completion_tokens if usage else 0,
            totalTokenCount=usage.total_tokens if usage else 0,
        ),
        modelVersion=model,
    )


def _stream_response(
    request: Request,
    provider: Upstream,
    canonical: CanonicalRequest,
    body: dict[str, Any],
    reservation: Reservation,
    trail: AuditTrail,
    *,
    sse: bool,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, for the google-genai SDK) or a JSON array (Gemini REST)."""

    async def generate_chunks() -> AsyncIterator[str]:
        # The same guarantee as the non-streaming path: whatever ends this generator — the stream
        # completing, an upstream failure, or the client hanging up — the reservation is resolved.
        async with request.app.state.budgets.hold(reservation):
            started = time.monotonic()
            parts: list[str] = []
            final_usage = None
            separator = ""
            # A stream that dies half way still has 200 in its (already sent) headers; the audit
            # record keeps the real outcome so the log does not claim a success that never
            # happened.
            status = 200
            try:
                if not sse:
                    yield "["
                try:
                    async for chunk in provider.stream_generate(canonical):
                        if chunk.usage is not None:
                            final_usage = chunk.usage
                        parts.append(chunk.text_delta)
                        payload = _chunk_to_gemini(chunk, canonical.model).model_dump_json()
                        if sse:
                            yield f"data: {payload}\n\n"
                        else:
                            yield f"{separator}{payload}"
                            separator = ","
                except UpstreamError as exc:
                    # Headers are already sent; log and terminate the stream cleanly.
                    status = _UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, "UNAVAILABLE"))[0]
                    _log.error(
                        "upstream_stream_error",
                        error=exc.message,
                        status=exc.status_code,
                        model=canonical.model,
                    )
                if not sse:
                    yield "]"
            finally:
                # Runs when the client hangs up too. The upstream was called either way, so the
                # request has to be accounted for and logged rather than vanishing from both.
                await _finish_stream(
                    request,
                    reservation,
                    trail,
                    model=canonical.model,
                    body=body,
                    text="".join(parts),
                    usage=final_usage,
                    status=status,
                    started=started,
                )

    media_type = "text/event-stream" if sse else "application/json"
    return StreamingResponse(generate_chunks(), media_type=media_type, headers=headers)


async def _finish_stream(
    request: Request,
    reservation: Reservation,
    trail: AuditTrail,
    *,
    model: str,
    body: dict[str, Any],
    text: str,
    usage: CanonicalUsage | None,
    status: int,
    started: float,
) -> None:
    """Account for a finished stream and write its audit row.

    A stream that reported no usage produced nothing chargeable, so it is *released* rather than
    settled — settling would still book one request, and a use case with a request limit would
    lose allowance to an upstream outage. Anything that did produce output is settled with what
    it actually used, including a stream that died half way.
    """
    cost = await request.app.state.pricing.cost_nanos(model, usage)
    if usage is None:
        await request.app.state.budgets.release(reservation)
    else:
        await request.app.state.budgets.settle(reservation, usage.total_tokens, cost_nanos=cost)
    await record_request(
        request,
        operation="streamGenerateContent",
        model=model,
        status=status,
        usage=usage,
        latency_ms=_elapsed_ms(started),
        request_payload=body,
        response_payload={"text": text},
        cost_nanos=cost,
        # A stream whose upstream died mid-flight has already sent a 200 header, so `status` is
        # the only place the failure survives — and the outcome has to agree with it, or the audit
        # would report a served request that produced an error.
        outcome=Outcome.SERVED if status == 200 else Outcome.UPSTREAM_ERROR,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        provenance=_provenance(request, trail.served_model),
    )
