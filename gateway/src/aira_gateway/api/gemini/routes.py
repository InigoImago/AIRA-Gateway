"""Gemini-compatible routes (`FRD-100`): ``POST /v1beta/models/{model}:{method}``.

Requests are validated against the Gemini schema, mapped to canonical, run through the shared
`api.serving` layer and mapped back; errors use the Gemini envelope. Every branch *raises* its
refusals and :func:`generate` records them in one place, so no branch can forget to (`FRD-122`).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from aira_common.logging import get_logger
from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    chunk_to_gemini,
    gemini_to_canonical,
    gemini_to_embedding,
    upstream_model_to_gemini,
)
from aira_gateway.api.gemini.refusals import refusal_response
from aira_gateway.api.serving import (
    EMBEDDING_METHODS,
    REFUSALS,
    Prepared,
    StreamedNotice,
    accounting,
    annotate,
    catalog_of,
    check_structured_result,
    declared_routing,
    deprecation_headers,
    ensure_body_is_encodable,
    json_body,
    prepare_for_dispatch,
    record_refusal,
    requirements_for,
    resolve_direct_target,
    schema_bounds,
    upstream_status,
)
from aira_gateway.audit import AuditTrail, Outcome
from aira_gateway.core.canonical import CanonicalEmbeddingRequest, CanonicalRequest
from aira_gateway.pipeline.dispatch import dispatch_with_fallback
from aira_gateway.state import providers_of
from aira_gateway.telemetry import model_call_chunks, model_call_span
from aira_gateway.upstreams.base import UpstreamError

_log = get_logger("aira_gateway")

router = APIRouter(tags=["gemini"])

#: The verbs that generate. The embedding verbs are `serving.EMBEDDING_METHODS`.
GENERATION_METHODS = frozenset({"generateContent", "streamGenerateContent"})


# == model listing ================================================================================


@router.get("/v1beta/models")
async def list_models(request: Request) -> JSONResponse:
    models = [
        await _described(request, upstream_model_to_gemini(m))
        for m in providers_of(request).models()
    ]
    # `exclude_none`: a limit nobody declared is an absent field, as with Google, never a null.
    return JSONResponse(schemas.ListModelsResponse(models=models).model_dump(exclude_none=True))


@router.get("/v1beta/models/{model}")
async def get_model(model: str, request: Request) -> Response:
    upstream_model = providers_of(request).get_model(model)
    if upstream_model is None:
        return _error(404, f"Model '{model}' not found.", "NOT_FOUND")
    described = await _described(request, upstream_model_to_gemini(upstream_model))
    return JSONResponse(described.model_dump(exclude_none=True))


async def _described(request: Request, model: schemas.GeminiModel) -> schemas.GeminiModel:
    """The model resource plus what the catalogue declares about it.

    From the catalogue, never from the vendor: a declaration is this installation's decision
    (`FRD-131`). An undeclared model is marked as such, the way an unpriced one is.
    """
    declaration = await catalog_of(request).declaration(model.name.removeprefix("models/"))
    return model.model_copy(
        update={
            "inputTokenLimit": declaration.context_window,
            "outputTokenLimit": declaration.max_output_tokens,
            "airaCapabilities": sorted(str(c) for c in declaration.capabilities),
            "airaMaxOutputTokens": declaration.max_output_tokens,
            "airaDeprecated": declaration.deprecated,
            "airaDeclared": declaration.declared,
        }
    )


# == the verbs ====================================================================================


@router.post("/v1beta/models/{resource}")
async def generate(resource: str, request: Request) -> Response:
    """Dispatch a Gemini verb, and record the request whether or not it was served."""
    model, _, method = split_resource(resource)
    trail = AuditTrail(operation=method or "unknown", requested_model=model, api="gemini")
    started = time.monotonic()
    try:
        return await _generate(resource, request, trail)
    except REFUSALS as exc:
        response = refusal_response(exc)
        await record_refusal(request, trail, exc, status=response.status_code, started=started)
        return response


def split_resource(resource: str) -> tuple[str, str, str]:
    """``model:method`` → its parts, split at the **last** colon.

    A model name may contain a colon (`qwen3:0.6b` on a self-hosted server); a method never does.
    """
    model, separator, method = resource.rpartition(":")
    return (model, separator, method) if separator else ("", "", resource)


async def _generate(resource: str, request: Request, trail: AuditTrail) -> Response:
    model, separator, method = split_resource(resource)
    if not separator:
        raise GeminiHTTPError(
            400, f"Missing method in '{resource}' (expected model:method).", "INVALID_ARGUMENT"
        )

    # The catalogue decides who serves a model, not only configuration (`FRD-507`); the publisher
    # counts because one platform hosts several wire formats.
    declared = await catalog_of(request).declaration(model)
    provider = providers_of(request).provider_for(model, declared.provider, declared.publisher)
    if provider is None:
        raise GeminiHTTPError(404, f"Model '{model}' not found.", "NOT_FOUND")

    body = await _read_body(request)
    trail.body = body

    canonical: CanonicalRequest | None = None
    embed_request: CanonicalEmbeddingRequest | None = None
    gemini_request: schemas.GenerateContentRequest | None = None
    if method in GENERATION_METHODS:
        gemini_request = _validated(schemas.GenerateContentRequest, body)
        canonical = gemini_to_canonical(model, gemini_request, bounds=schema_bounds(request))
    elif method in EMBEDDING_METHODS:
        embed_request = _embedding_request(model, method, body)
    else:
        raise GeminiHTTPError(400, f"Unknown method '{method}'.", "INVALID_ARGUMENT")

    prepared = await prepare_for_dispatch(
        request,
        trail,
        method=method,
        canonical=canonical,
        embed=embed_request,
        requested_output=canonical.max_output_tokens if canonical is not None else None,
        reasoning_asked_for=_asked_for_reasoning(gemini_request),
    )
    headers = deprecation_headers(prepared.declaration)
    if prepared.canonical is None:
        return await _embed(request, model, method, prepared, trail, headers)
    if method == "generateContent":
        return await _generate_content(request, prepared, trail, headers)
    return await _stream_response(
        request,
        prepared.canonical,
        prepared,
        trail,
        sse=request.query_params.get("alt") == "sse",
        headers=headers,
    )


async def _generate_content(
    request: Request, prepared: Prepared, trail: AuditTrail, headers: dict[str, str]
) -> Response:
    canonical = prepared.canonical
    assert canonical is not None
    started = time.monotonic()
    async with accounting(
        request, trail, prepared, api="gemini", operation="generateContent", started=started
    ) as acct:
        dispatched = await dispatch_with_fallback(
            providers_of(request),
            canonical,
            prepared.fallbacks,
            permits=await requirements_for(request, canonical),
            routing_of=await declared_routing(request),
        )
        answer = annotate(canonical, dispatched.response, prepared, trail)
        trail.served_by(answer.model, dispatched.candidate_index)
        trail.passed_over(dispatched.skipped)
        # Before the outcome is reported, so a refused answer is released rather than booked.
        check_structured_result(canonical, answer)
        # `exclude_none`: Google omits what did not happen (no `functionCall: null`, no zero
        # `thoughtsTokenCount`), and a compatible surface must not invent those fields.
        payload = canonical_to_gemini(answer).model_dump(exclude_none=True)
        acct.served(answer.model, answer.usage, payload, [call.name for call in answer.tool_calls])
    return JSONResponse(payload, headers=headers)


async def _embed(
    request: Request,
    model: str,
    method: str,
    prepared: Prepared,
    trail: AuditTrail,
    headers: dict[str, str],
) -> Response:
    embed_request = prepared.embed
    assert embed_request is not None
    # An embedding has no chain to carry the conditions, so they are applied here.
    provider = await resolve_direct_target(request, str(embed_request.model))
    started = time.monotonic()
    async with accounting(
        request, trail, prepared, api="gemini", operation=method, started=started
    ) as acct:
        with model_call_span(str(embed_request.model), purpose="embed"):
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
    return JSONResponse(payload, headers=headers)


async def _stream_response(
    request: Request,
    canonical: CanonicalRequest,
    prepared: Prepared,
    trail: AuditTrail,
    *,
    sse: bool,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, the google-genai SDK) or as a JSON array (Gemini REST).

    Resolves its adapter **before** the response exists: resolving is what applies the dispatch
    conditions, and a refusal must still be a status rather than a stream that stops.
    """
    provider = await resolve_direct_target(request, canonical.model, canonical)

    async def generate_chunks() -> AsyncIterator[str]:
        started = time.monotonic()
        async with accounting(
            request,
            trail,
            prepared,
            api="gemini",
            operation="streamGenerateContent",
            started=started,
        ) as acct:
            parts: list[str] = []
            streamed_calls: list[str] = []
            final_usage = None
            separator = ""
            # Overridden only by an upstream failure. A caller who hangs up leaves the
            # `Accounting` default (499), exactly as on the KIRA surface.
            status = 200
            notice = StreamedNotice(
                prepared.notices, structured=canonical.response_schema is not None
            )
            try:
                if not sse:
                    yield "["
                try:
                    async for chunk in model_call_chunks(
                        canonical.model, provider.stream_generate(canonical)
                    ):
                        if chunk.usage is not None:
                            final_usage = chunk.usage
                        streamed_calls.extend(call.name for call in chunk.tool_calls)
                        # The audit row accumulates exactly what the caller receives.
                        led = notice.lead(chunk.text_delta)
                        parts.append(led)
                        payload = chunk_to_gemini(
                            chunk.model_copy(update={"text_delta": led}), canonical.model
                        ).model_dump_json(exclude_none=True)
                        if sse:
                            yield f"data: {payload}\n\n"
                        else:
                            yield f"{separator}{payload}"
                            separator = ","
                except UpstreamError as exc:
                    # Headers are already sent: log it and end the stream cleanly.
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
                outcome_note = notice.outcome()
                if outcome_note is not None:
                    trail.decisions.append(outcome_note)
                # No usage reported means nothing chargeable, so the reservation is released.
                if final_usage is not None:
                    # The call names too: a streamed tool call has no text to accumulate.
                    acct.served(
                        canonical.model, final_usage, {"text": "".join(parts)}, streamed_calls
                    )
                if status != 200:
                    acct.status = status
                    acct.outcome = Outcome.UPSTREAM_ERROR

    media_type = "text/event-stream" if sse else "application/json"
    return StreamingResponse(generate_chunks(), media_type=media_type, headers=headers)


# == parsing ======================================================================================


async def _read_body(request: Request) -> dict[str, Any]:
    try:
        body = await json_body(request)
        ensure_body_is_encodable(body)
    except ValueError:
        raise GeminiHTTPError(400, "Request body is not valid JSON.", "INVALID_ARGUMENT") from None
    if not isinstance(body, dict):
        # Refused before it reaches the trail: the audit row stores an object, so a list or a
        # string would fail the write and leave no row (`FRD-122`).
        raise GeminiHTTPError(400, "Send one JSON object.", "INVALID_ARGUMENT")
    return body


def _validated[Model: BaseModel](schema: type[Model], body: dict[str, Any]) -> Model:
    try:
        return schema.model_validate(body)
    except ValidationError as exc:
        raise GeminiHTTPError(400, _first_error(exc), "INVALID_ARGUMENT") from exc


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}".strip(": ")


def _embedding_request(model: str, method: str, body: dict[str, Any]) -> CanonicalEmbeddingRequest:
    entries = (
        [_validated(schemas.EmbedContentRequest, body)]
        if method == "embedContent"
        else _validated(schemas.BatchEmbedContentsRequest, body).requests
    )
    # The URL chooses the model. An entry naming a different one is refused rather than ignored,
    # or the caller would get another model's vector under a 200 (`FRD-124`).
    for entry in entries:
        if not schemas.names_the_same_model(entry.model, model):
            raise GeminiHTTPError(
                400,
                f"An embedding request names model '{entry.model}' while the URL addresses "
                f"'{model}'. The URL decides which model serves a request; remove the field "
                "or address the model you meant.",
                "INVALID_ARGUMENT",
            )
    return gemini_to_embedding(model, entries)


def _asked_for_reasoning(parsed: schemas.GenerateContentRequest | None) -> bool:
    """This surface's spelling of "give me the model's reasoning" (`FRD-135` FR-4)."""
    if parsed is None or parsed.generationConfig is None:
        return False
    thinking = parsed.generationConfig.thinkingConfig
    return bool(thinking is not None and thinking.includeThoughts)
