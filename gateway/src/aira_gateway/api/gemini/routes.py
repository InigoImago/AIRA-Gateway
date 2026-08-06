"""Gemini-compatible routes (FRD-100).

Mirrors Google's colon-verb convention: ``POST /v1beta/models/{model}:{method}``. Requests
are validated against the Gemini schema, mapped to canonical, dispatched to the resolved
provider, and mapped back. Errors use the Gemini error envelope.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from aira_common.logging import get_logger
from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    gemini_to_canonical,
    gemini_to_embedding,
    upstream_model_to_gemini,
)
from aira_gateway.api.serving import (
    REFUSALS,
    UPSTREAM_STATUS_MAP,
    catalog_of,
    check_declaration,
    check_structured_result,
    deprecation_headers,
    elapsed_ms,
    embedding_bounds,
    enforce_pre_dispatch,
    provenance,
    refusal_outcome,
    registry_of,
    requirements_for,
    run_pipeline,
    schema_bounds,
    upstream_error,
)
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import Reservation
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalUsage,
)
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.embedding import estimated_tokens as embedding_tokens
from aira_gateway.embedding import validate as validate_embedding
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.thinking import ThinkingRejected, reserved_tokens
from aira_gateway.thinking import resolve as resolve_thinking
from aira_gateway.upstreams.base import Upstream, UpstreamError

_log = get_logger("aira_gateway")


router = APIRouter(tags=["gemini"])


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}".strip(": ")


async def _described(request: Request, model: schemas.GeminiModel) -> schemas.GeminiModel:
    """Attach what the catalog declares, so the list says what each model may be asked to do."""
    declaration = await catalog_of(request).declaration(model.name.removeprefix("models/"))
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
        await _described(request, upstream_model_to_gemini(m))
        for m in registry_of(request).models()
    ]
    return JSONResponse(schemas.ListModelsResponse(models=models).model_dump())


@router.get("/v1beta/models/{model}")
async def get_model(model: str, request: Request) -> Response:
    upstream_model = registry_of(request).get_model(model)
    if upstream_model is None:
        return _error(404, f"Model '{model}' not found.", "NOT_FOUND")
    described = await _described(request, upstream_model_to_gemini(upstream_model))
    return JSONResponse(described.model_dump())


#: How a refusal maps onto the closed outcome vocabulary. Anything unmapped is a bug in this
#: table, not a reason to record nothing — see ``_refusal_outcome``.


def _refusal_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AttachmentRejected | SchemaRejected):
        return _error(400, str(exc), "INVALID_ARGUMENT")
    if isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # The code the predecessor uses travels in the message, because Google's envelope has no
        # field for it and inventing one would make this surface non-Gemini. The KIRA surface,
        # whose clients switch on the code, renders it as the code (`FRD-107`).
        return _error(400, f"{exc.code}: {exc.message}", "INVALID_ARGUMENT")
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
        return upstream_error(exc)
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
    except REFUSALS as exc:
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
            outcome=str(refusal_outcome(exc)),
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
        latency_ms=elapsed_ms(started),
        request_payload=trail.body,
        response_payload=None,
        outcome=refusal_outcome(exc),
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        provenance=provenance(request, trail.served_model),
    )


async def _generate(resource: str, request: Request, trail: AuditTrail) -> Response:
    model, separator, method = resource.partition(":")
    if not separator:
        raise GeminiHTTPError(
            400, f"Missing method in '{resource}' (expected model:method).", "INVALID_ARGUMENT"
        )

    provider = registry_of(request).provider_for(model)
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
    embed_request: CanonicalEmbeddingRequest | None = None

    if method in ("generateContent", "streamGenerateContent"):
        try:
            gemini_request = schemas.GenerateContentRequest.model_validate(body)
        except ValidationError as exc:
            raise GeminiHTTPError(400, _first_error(exc), "INVALID_ARGUMENT") from exc
        canonical = gemini_to_canonical(model, gemini_request, bounds=schema_bounds(request))

        canonical, fallbacks = await run_pipeline(request, canonical, trail)

        # Routing may have changed the model — resolve the effective provider.
        provider = registry_of(request).provider_for(canonical.model)
        if provider is None:
            raise GeminiHTTPError(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")
    elif method in ("embedContent", "batchEmbedContents"):
        try:
            entries = (
                [schemas.EmbedContentRequest.model_validate(body)]
                if method == "embedContent"
                else schemas.BatchEmbedContentsRequest.model_validate(body).requests
            )
        except ValidationError as exc:
            raise GeminiHTTPError(400, _first_error(exc), "INVALID_ARGUMENT") from exc
        embed_request = gemini_to_embedding(model, entries)
    else:
        raise GeminiHTTPError(400, f"Unknown method '{method}'.", "INVALID_ARGUMENT")

    # One gate for every method. Keeping these inside the generateContent branch is how
    # `:embedContent` ended up unlimited and unbudgeted — a caller only had to pick the other
    # verb. A control that applies to some verbs and not others has to be impossible to write
    # by accident, so there is exactly one place to add the next one.
    effective_model = canonical.model if canonical is not None else model
    requested_output = canonical.max_output_tokens if canonical is not None else None
    declaration = await check_declaration(
        request, model=effective_model, method=method, requested=requested_output
    )

    if canonical is not None:
        # Resolved against the model that will actually serve this — after routing, before the
        # reservation. The order is load-bearing: the number sent upstream and the number reserved
        # against the budget have to be the same one, or the limit is bounding a different request
        # than the one that was made (`FRD-111` §5.3).
        canonical = canonical.model_copy(
            update={"thinking": resolve_thinking(canonical.thinking, declaration)}
        )
    if embed_request is not None:
        embed_request = validate_embedding(embed_request, declaration, embedding_bounds(request))

    units = embed_request.size if embed_request is not None else 1
    reservation = await enforce_pre_dispatch(
        request,
        model=effective_model,
        max_output_tokens=requested_output,
        attachments=[part.media_type for part in canonical.attachments] if canonical else None,
        units=units,
        extra_tokens=(
            reserved_tokens(canonical.thinking)
            if canonical is not None
            else embedding_tokens(embed_request)
            if embed_request is not None
            else 0
        ),
    )

    if canonical is not None:
        if method == "generateContent":
            # `hold` releases the reservation unless it is settled inside the block, so no exit
            # path — an upstream outage, a pricing lookup that fails, an outright bug — can leave
            # budget consumed by a request that produced nothing (FRD-405 FR-5).
            async with request.app.state.budgets.hold(reservation):
                started = time.monotonic()
                dispatched = await dispatch_with_fallback(
                    registry_of(request),
                    canonical,
                    fallbacks,
                    permits=requirements_for(request, canonical),
                )
                canonical_response = dispatched.response
                trail.served_by(canonical_response.model, dispatched.candidate_index)
                trail.passed_over(dispatched.skipped)
                # A truncated or unsatisfied document is not data (FRD-112 FR-6). Checked before
                # the reservation is settled, so a refused answer is released rather than booked.
                check_structured_result(canonical, canonical_response)
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
                    latency_ms=elapsed_ms(started),
                    request_payload=body,
                    response_payload=payload,
                    cost_nanos=cost,
                    requested_model=trail.requested_model,
                    model_selection=trail.selection,
                    pipeline_decisions=decision_summary(trail.decisions),
                    provenance=provenance(request, canonical_response.model),
                )
                return JSONResponse(payload, headers=deprecation_headers(declaration))
        sse = request.query_params.get("alt") == "sse"
        return _stream_response(
            request,
            provider,
            canonical,
            body,
            reservation,
            trail,
            sse=sse,
            headers=deprecation_headers(declaration),
        )

    assert embed_request is not None  # the method dispatch above guarantees it
    async with request.app.state.budgets.hold(reservation):
        started = time.monotonic()
        vectors = await provider.embed(embed_request)
        payload = (
            schemas.BatchEmbedContentsResponse(
                embeddings=[schemas.ContentEmbedding(values=values) for values in vectors]
            ).model_dump()
            if method == "batchEmbedContents"
            else schemas.EmbedContentResponse(
                embedding=schemas.ContentEmbedding(values=vectors[0] if vectors else [])
            ).model_dump()
        )
        # Embeddings report no token usage, so there is nothing to price. The batch still counts
        # as the *many* requests it is against a request-limited budget — settling it as one would
        # hand back everything the reservation took and leave batched traffic invisible.
        await request.app.state.budgets.settle(
            reservation, 0, cost_nanos=None, requests=embed_request.size
        )
        await record_request(
            request,
            operation=method,
            model=model,
            status=200,
            usage=None,
            latency_ms=elapsed_ms(started),
            request_payload=body,
            response_payload=payload,
            requested_model=trail.requested_model,
            provenance=provenance(request, model),
        )
        return JSONResponse(payload, headers=deprecation_headers(declaration))


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
                    status = UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, "UNAVAILABLE"))[0]
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
                #
                # **Shielded**, and the reason is subtle enough to be worth stating. Closing the
                # generator from inside the process raises `GeneratorExit`, and awaits in a
                # `finally` then run normally — which is why the hermetic disconnect test passes
                # deterministically. A client dropping a real socket cancels the response task
                # instead, and a bare `await` here re-raises `CancelledError` at its first
                # suspension point: the settle and the audit row are simply lost. That showed up as
                # a 1-in-8 flake in the integration suite, which is what a lost row looks like from
                # the outside.
                #
                # `shield` lets the accounting run to completion while the cancellation propagates
                # past it. Losing the row is the failure `FRD-405` B4 promised not to have.
                await asyncio.shield(
                    _finish_stream(
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
        latency_ms=elapsed_ms(started),
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
        provenance=provenance(request, trail.served_model),
    )
