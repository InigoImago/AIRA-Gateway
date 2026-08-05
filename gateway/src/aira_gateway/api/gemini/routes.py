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
from aira_common.money import cost_nanos
from aira_common.observability import set_span_attributes
from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    gemini_to_canonical,
    upstream_model_to_gemini,
)
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import dispatch_with_fallback
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.errors import RateLimited
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


async def _estimate(request: Request, canonical: CanonicalRequest) -> Amounts:
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
    tokens = canonical.max_output_tokens or settings.budget_estimate_output_tokens
    price = await request.app.state.pricing.price_for(canonical.model)
    cost = 0 if price is None else cost_nanos(tokens, price.output_per_million_nanos)
    return Amounts(tokens=tokens, requests=1, cost_nanos=cost)


def _upstream_error(exc: UpstreamError) -> JSONResponse:
    code, status = _UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, "UNAVAILABLE"))
    return _error(code, exc.message, status)


def _registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


async def _run_pipeline(
    request: Request, canonical: CanonicalRequest
) -> tuple[CanonicalRequest, tuple[str, ...]]:
    """Apply the use case's pre-dispatch pipeline (FRD-300). Pass-through when none is configured.

    Returns the effective request (possibly re-routed) and the dispatch fallback chain. May raise
    ``PipelineRejected`` when a filter/allow-check blocks the request.
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return canonical, ()
    outcome = await engine.run(pipeline, canonical)
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


@router.get("/v1beta/models")
async def list_models(request: Request) -> JSONResponse:
    models = [upstream_model_to_gemini(m) for m in _registry(request).models()]
    return JSONResponse(schemas.ListModelsResponse(models=models).model_dump())


@router.get("/v1beta/models/{model}")
async def get_model(model: str, request: Request) -> Response:
    upstream_model = _registry(request).get_model(model)
    if upstream_model is None:
        return _error(404, f"Model '{model}' not found.", "NOT_FOUND")
    return JSONResponse(upstream_model_to_gemini(upstream_model).model_dump())


@router.post("/v1beta/models/{resource}")
async def generate(resource: str, request: Request) -> Response:
    model, separator, method = resource.partition(":")
    if not separator:
        return _error(
            400, f"Missing method in '{resource}' (expected model:method).", "INVALID_ARGUMENT"
        )

    provider = _registry(request).provider_for(model)
    if provider is None:
        return _error(404, f"Model '{model}' not found.", "NOT_FOUND")

    try:
        body = await request.json()
    except ValueError:
        return _error(400, "Request body is not valid JSON.", "INVALID_ARGUMENT")

    if method in ("generateContent", "streamGenerateContent"):
        try:
            gemini_request = schemas.GenerateContentRequest.model_validate(body)
        except ValidationError as exc:
            return _error(400, _first_error(exc), "INVALID_ARGUMENT")
        canonical = gemini_to_canonical(model, gemini_request)

        try:
            canonical, fallbacks = await _run_pipeline(request, canonical)
        except PipelineRejected as exc:
            return _error(exc.code, exc.message, exc.status)

        # Routing may have changed the model — resolve the effective provider.
        provider = _registry(request).provider_for(canonical.model)
        if provider is None:
            return _error(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")

        attribution = getattr(request.state, "attribution", None)
        use_case = getattr(attribution, "use_case", None)
        subject = getattr(attribution, "subject", None)

        # Rate limiting (FRD-405) runs first: the point of a limit is that the expensive part of
        # the request — the upstream call — never happens.
        try:
            await request.app.state.rate_limits.check(use_case, subject)
        except RateLimited as exc:
            return _error(
                429,
                exc.message,
                "RESOURCE_EXHAUSTED",
                headers={"Retry-After": exc.retry_after},
            )

        # Budget enforcement (FRD-401/405): reserve before dispatch, so requests in flight are
        # visible to each other's check instead of all passing the same stale figure.
        estimate = await _estimate(request, canonical)
        try:
            reservation = await request.app.state.budgets.guard(
                use_case, subject, estimated=estimate
            )
        except BudgetExceeded as exc:
            return _error(429, exc.message, "RESOURCE_EXHAUSTED")

        if method == "generateContent":
            started = time.monotonic()
            try:
                canonical_response = await dispatch_with_fallback(
                    _registry(request), canonical, fallbacks
                )
            except UpstreamError as exc:
                # The request produced nothing chargeable; keeping the reservation would make a
                # provider outage indistinguishable from having spent the month's budget.
                await request.app.state.budgets.release(reservation)
                return _upstream_error(exc)
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
            )
            return JSONResponse(payload)
        sse = request.query_params.get("alt") == "sse"
        return _stream_response(request, provider, canonical, body, reservation, sse=sse)

    if method == "embedContent":
        try:
            embed_request = schemas.EmbedContentRequest.model_validate(body)
        except ValidationError as exc:
            return _error(400, _first_error(exc), "INVALID_ARGUMENT")
        started = time.monotonic()
        text = "".join(part.text for part in embed_request.content.parts)
        try:
            values = await provider.embed(model, text)
        except UpstreamError as exc:
            return _upstream_error(exc)
        payload = schemas.EmbedContentResponse(
            embedding=schemas.ContentEmbedding(values=values)
        ).model_dump()
        await record_request(
            request,
            operation="embedContent",
            model=model,
            status=200,
            usage=None,
            latency_ms=_elapsed_ms(started),
            request_payload=body,
            response_payload=payload,
        )
        return JSONResponse(payload)

    return _error(400, f"Unknown method '{method}'.", "INVALID_ARGUMENT")


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
    *,
    sse: bool,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, for the google-genai SDK) or a JSON array (Gemini REST)."""

    async def generate_chunks() -> AsyncIterator[str]:
        started = time.monotonic()
        parts: list[str] = []
        final_usage = None
        separator = ""
        # A stream that dies half way still has 200 in its (already sent) headers; the audit
        # record keeps the real outcome so the log does not claim a success that never happened.
        status = 200
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
        cost = await request.app.state.pricing.cost_nanos(canonical.model, final_usage)
        await request.app.state.budgets.settle(
            reservation, final_usage.total_tokens if final_usage else 0, cost_nanos=cost
        )
        await record_request(
            request,
            operation="streamGenerateContent",
            model=canonical.model,
            status=status,
            usage=final_usage,
            latency_ms=_elapsed_ms(started),
            request_payload=body,
            response_payload={"text": "".join(parts)},
            cost_nanos=cost,
        )

    media_type = "text/event-stream" if sse else "application/json"
    return StreamingResponse(generate_chunks(), media_type=media_type)
