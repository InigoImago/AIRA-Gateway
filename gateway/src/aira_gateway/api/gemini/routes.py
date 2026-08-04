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

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    gemini_to_canonical,
    upstream_model_to_gemini,
)
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest
from aira_gateway.persistence.recorder import record_request
from aira_gateway.upstreams.base import ProviderRegistry, Upstream


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


router = APIRouter(tags=["gemini"])


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}".strip(": ")


def _registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


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
        if method == "generateContent":
            started = time.monotonic()
            canonical_response = provider.generate(canonical)
            payload = canonical_to_gemini(canonical_response).model_dump()
            await record_request(
                request,
                operation="generateContent",
                model=model,
                status=200,
                usage=canonical_response.usage,
                latency_ms=_elapsed_ms(started),
                request_payload=body,
                response_payload=payload,
            )
            return JSONResponse(payload)
        sse = request.query_params.get("alt") == "sse"
        return _stream_response(request, provider, canonical, body, sse=sse)

    if method == "embedContent":
        try:
            embed_request = schemas.EmbedContentRequest.model_validate(body)
        except ValidationError as exc:
            return _error(400, _first_error(exc), "INVALID_ARGUMENT")
        started = time.monotonic()
        text = "".join(part.text for part in embed_request.content.parts)
        values = provider.embed(model, text)
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
    *,
    sse: bool,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, for the google-genai SDK) or a JSON array (Gemini REST)."""

    async def generate_chunks() -> AsyncIterator[str]:
        started = time.monotonic()
        parts: list[str] = []
        final_usage = None
        separator = ""
        if not sse:
            yield "["
        for chunk in provider.stream_generate(canonical):
            if chunk.usage is not None:
                final_usage = chunk.usage
            parts.append(chunk.text_delta)
            payload = _chunk_to_gemini(chunk, canonical.model).model_dump_json()
            if sse:
                yield f"data: {payload}\n\n"
            else:
                yield f"{separator}{payload}"
                separator = ","
        if not sse:
            yield "]"
        await record_request(
            request,
            operation="streamGenerateContent",
            model=canonical.model,
            status=200,
            usage=final_usage,
            latency_ms=_elapsed_ms(started),
            request_payload=body,
            response_payload={"text": "".join(parts)},
        )

    media_type = "text/event-stream" if sse else "application/json"
    return StreamingResponse(generate_chunks(), media_type=media_type)
