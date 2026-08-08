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
from aira_gateway.anomalies.suspensions import Suspended
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
    Prepared,
    accounting,
    catalog_of,
    check_structured_result,
    deprecation_headers,
    elapsed_ms,
    prepare_for_dispatch,
    provenance,
    refusal_outcome,
    registry_of,
    requirements_for,
    schema_bounds,
    upstream_error,
    upstream_status,
)
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
)
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import Upstream, UpstreamError

_log = get_logger("aira_gateway")


router = APIRouter(tags=["gemini"])


def split_resource(resource: str) -> tuple[str, str, str]:
    """``model:method`` → the two parts, splitting at the **last** colon.

    Not the first. Google's model names carry none, so `partition` was correct for as long as
    Google was the only vendor — and then a self-hosted server arrived whose names are
    `qwen3:0.6b` and `llama3.1:70b`. Splitting at the first colon turned that into the model
    `qwen3` and the method `0.6b:generateContent`, which surfaced as **"Model \'qwen3\' not
    found"**: a message naming a model the caller never asked for, pointing at the catalog
    instead of at the parser.

    The method never contains a colon and the model may, so the last one is the separator. Found
    the first time a real request was sent to a real local model, which is the entire argument for
    having one (`FRD-123`).
    """
    model, separator, method = resource.rpartition(":")
    return (model, separator, method) if separator else ("", "", resource)


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
    if isinstance(exc, Suspended):
        # 429, not 403. The credential is valid and the membership is real; the caller is stopped
        # *temporarily*, and "come back later" is what 429 means. A 403 would send a client off to
        # fix permissions it has no problem with.
        return _error(
            429, exc.message, "RESOURCE_EXHAUSTED", headers={"Retry-After": exc.retry_after}
        )
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
    model, _, method = split_resource(resource)
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
    model, separator, method = split_resource(resource)
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

    # Before the branch, so **every** verb takes it: the controls that need no model. Below the
    # branch is where a generate request runs its pipeline, and the pipeline can call a model —
    # so a caller refused after that point has already been charged for the refusal.
    #
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

    # The whole pre-dispatch sequence, in the one place that owns its order (`serving.py`). This
    # used to be six calls written out here and six more written out in the KIRA surface — the
    # same order, twice, and a third surface would have been a third copy. Every guarantee this
    # layer makes is a guarantee about that order, so the order is not a surface's to assemble.
    prepared = await prepare_for_dispatch(
        request,
        trail,
        method=method,
        canonical=canonical,
        embed=embed_request,
        requested_output=canonical.max_output_tokens if canonical is not None else None,
    )
    canonical = prepared.canonical
    embed_request = prepared.embed
    fallbacks = prepared.fallbacks
    declaration = prepared.declaration

    if canonical is not None:
        if method == "generateContent":
            started = time.monotonic()
            # The post-dispatch sequence, in the one place that owns it. It holds the reservation
            # and accounts for the request **however it ends** — including a caller who goes away
            # while the model is still answering, which used to leave no row at all (`FRD-128`).
            async with accounting(
                request,
                trail,
                prepared,
                api="gemini",
                operation="generateContent",
                body=body,
                started=started,
            ) as acct:
                dispatched = await dispatch_with_fallback(
                    registry_of(request),
                    canonical,
                    fallbacks,
                    permits=requirements_for(request, canonical),
                )
                canonical_response = dispatched.response
                trail.served_by(canonical_response.model, dispatched.candidate_index)
                trail.passed_over(dispatched.skipped)
                # A truncated or unsatisfied document is not data (FRD-112 FR-6). Raised before
                # the outcome is reported, so a refused answer is released rather than booked.
                check_structured_result(canonical, canonical_response)
                payload = canonical_to_gemini(canonical_response).model_dump()
                acct.served(
                    canonical_response.model,
                    canonical_response.usage,
                    payload,
                    [call.name for call in canonical_response.tool_calls],
                )
            return JSONResponse(payload, headers=deprecation_headers(declaration))
        sse = request.query_params.get("alt") == "sse"
        return _stream_response(
            request,
            provider,
            canonical,
            body,
            prepared,
            trail,
            sse=sse,
            headers=deprecation_headers(declaration),
        )

    assert embed_request is not None  # the method dispatch above guarantees it
    started = time.monotonic()
    async with accounting(
        request, trail, prepared, api="gemini", operation=method, body=body, started=started
    ) as acct:
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
        acct.embedded(model, payload, units=embed_request.size)
    return JSONResponse(payload, headers=deprecation_headers(declaration))


def _chunk_to_gemini(chunk: CanonicalChunk, model: str) -> schemas.GenerateContentResponse:
    usage = chunk.usage
    # A chunk carries a text delta, or completed tool calls, or both. The calls arrive whole on
    # the chunk that ends the message (`FRD-131` FR-6) — never in pieces, because half a function
    # call is not a smaller function call. Without this the client sees the answer's *tokens*
    # streamed and never the call itself, which for an assistant is the entire answer.
    parts: list[schemas.Part] = []
    if chunk.text_delta or not chunk.tool_calls:
        parts.append(schemas.Part(text=chunk.text_delta))
    parts.extend(
        schemas.Part(
            functionCall=schemas.FunctionCall(name=call.name, args=call.arguments, id=call.id)
        )
        for call in chunk.tool_calls
    )
    return schemas.GenerateContentResponse(
        candidates=[
            schemas.Candidate(
                content=schemas.Content(role="model", parts=parts),
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
    prepared: Prepared,
    trail: AuditTrail,
    *,
    sse: bool,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, for the google-genai SDK) or a JSON array (Gemini REST)."""

    async def generate_chunks() -> AsyncIterator[str]:
        started = time.monotonic()
        async with accounting(
            request,
            trail,
            prepared,
            api="gemini",
            operation="streamGenerateContent",
            body=body,
            started=started,
        ) as acct:
            parts: list[str] = []
            #: Names only, and accumulated across chunks because a provider may finish several
            #: calls at different moments. `FRD-131` FR-7.
            streamed_calls: list[str] = []
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
                        streamed_calls.extend(call.name for call in chunk.tool_calls)
                        parts.append(chunk.text_delta)
                        payload = _chunk_to_gemini(chunk, canonical.model).model_dump_json()
                        if sse:
                            yield f"data: {payload}\n\n"
                        else:
                            yield f"{separator}{payload}"
                            separator = ","
                except UpstreamError as exc:
                    # Headers are already sent; log and terminate the stream cleanly.
                    status = upstream_status(exc.status_code)[0]
                    _log.error(
                        "upstream_stream_error",
                        error=exc.message,
                        status=exc.status_code,
                        model=canonical.model,
                    )
                if not sse:
                    yield "]"
            finally:
                # Reported into the shared sequence, which owns the shielded settle-and-record for
                # every path — this one included. A stream that reported no usage produced nothing
                # chargeable and is *released*: settling would still book one request, and a use
                # case with a request limit would lose allowance to an upstream outage.
                if final_usage is not None:
                    # The names as well as the text. A streamed tool call has **no text delta** to
                    # accumulate — the answer *is* the call — so a row built from `parts` alone
                    # reads `{"text": ""}` and the audit trail has nothing about what the model
                    # asked to have run. Measured on a real assistant turn before this line existed.
                    acct.served(
                        canonical.model, final_usage, {"text": "".join(parts)}, streamed_calls
                    )
                acct.status = status
                if status != 200:
                    acct.outcome = Outcome.UPSTREAM_ERROR

    media_type = "text/event-stream" if sse else "application/json"
    return StreamingResponse(generate_chunks(), media_type=media_type, headers=headers)
