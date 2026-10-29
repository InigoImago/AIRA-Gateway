"""Gemini-compatible routes (`FRD-100`): ``POST /v1beta/models/{model}:{method}``.

Requests are validated against the Gemini schema, mapped to canonical, run through the shared
`api.serving` layer and mapped back; errors use the Gemini envelope. Every branch *raises* its
refusals and :func:`generate` records them in one place, so no branch can forget to (`FRD-122`).
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    chunk_to_gemini,
    gemini_to_canonical,
    gemini_to_embedding,
    upstream_model_to_gemini,
    without_signatures,
)
from aira_gateway.api.gemini.refusals import refusal_response
from aira_gateway.api.serving import (
    EMBEDDING_METHODS,
    REFUSALS,
    STREAM_HEADERS,
    TICK,
    AccountedStream,
    Opening,
    Prepared,
    StreamAccounting,
    accounting,
    annotate,
    catalog_of,
    check_structured_result,
    declared_routing,
    deprecation_headers,
    embed_along_chain,
    ensure_body_is_encodable,
    fitting,
    json_body,
    keepalive_seconds,
    prepare_for_dispatch,
    record_refusal,
    requirements_for,
    schema_bounds,
    served_models,
    with_pings,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
)
from aira_gateway.pipeline.dispatch import dispatch_with_fallback, stream_with_fallback
from aira_gateway.state import providers_of

router = APIRouter(tags=["gemini"])

#: The verbs that generate. The embedding verbs are `serving.EMBEDDING_METHODS`.
GENERATION_METHODS = frozenset({"generateContent", "streamGenerateContent"})

#: What a quiet stream sends to keep its connection (`ADR-0046`): **a bare line**. In SSE a line
#: with nothing on it ends an event, and with no event pending it ends nothing, so a client
#: dispatches nothing; between the elements of the JSON array it is whitespace, as Google itself
#: writes between them. Not an SSE comment, which is what the KIRA surface sends: the `google-genai`
#: SDK, every version, hands any line that is not `data:` to its JSON parser and raises. Its
#: JavaScript twin skips a bare line from 1.21 (September 2025) and stalls on one before that.
KEEPALIVE = "\n"


# == model listing ================================================================================


@router.get("/v1beta/models")
async def list_models(request: Request) -> JSONResponse:
    models = [
        await _described(request, upstream_model_to_gemini(m)) for m in await served_models(request)
    ]
    # `exclude_none`: a limit nobody declared is an absent field, as with Google, never a null.
    return JSONResponse(schemas.ListModelsResponse(models=models).model_dump(exclude_none=True))


@router.get("/v1beta/models/{model}")
async def get_model(model: str, request: Request) -> Response:
    upstream_model = next((m for m in await served_models(request) if m.name == model), None)
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
        response = refusal_response(exc, request)
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
    asked = prepared.asked
    assert asked is not None
    return await _stream_response(
        request,
        asked,
        prepared,
        trail,
        sse=request.query_params.get("alt") == "sse",
        headers=headers,
    )


async def _generate_content(
    request: Request, prepared: Prepared, trail: AuditTrail, headers: dict[str, str]
) -> Response:
    canonical, asked = prepared.canonical, prepared.asked
    assert canonical is not None and asked is not None
    started = time.monotonic()
    async with accounting(
        request, trail, prepared, api="gemini", operation="generateContent", started=started
    ) as acct:
        # The caller's request, fitted to each candidate in turn: a fallback is held to what the
        # caller asked and given its own defaults, never the primary's (`fitting`).
        dispatched = await dispatch_with_fallback(
            providers_of(request),
            asked,
            prepared.fallbacks,
            permits=await requirements_for(request, asked),
            routing_of=await declared_routing(request),
            fit=fitting(request),
        )
        answer = annotate(canonical, dispatched.response, prepared, trail)
        trail.served_by(answer.model, dispatched.candidate_index)
        trail.passed_over(dispatched.skipped, dispatched.failed)
        # Spent before it is checked: a document the model did not finish is refused, and was
        # paid for all the same.
        acct.spent(answer.model, answer.usage)
        check_structured_result(canonical, answer)
        # `exclude_none`: Google omits what did not happen (no `functionCall: null`, no zero
        # `thoughtsTokenCount`), and a compatible surface must not invent those fields.
        payload = canonical_to_gemini(answer).model_dump(exclude_none=True)
        # Stored without the model's signatures: state the caller needs, not what was said.
        stored = without_signatures(payload)
        acct.served(answer.model, answer.usage, stored, [call.name for call in answer.tool_calls])
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
    started = time.monotonic()
    async with accounting(
        request, trail, prepared, api="gemini", operation=method, started=started
    ) as acct:
        # Along the embedding's chain, every candidate meeting every condition (`FRD-310`).
        vectors = await embed_along_chain(request, prepared, trail)
        payload = (
            schemas.BatchEmbedContentsResponse(
                embeddings=[schemas.ContentEmbedding(values=values) for values in vectors]
            ).model_dump()
            if method == "batchEmbedContents"
            else schemas.EmbedContentResponse(
                embedding=schemas.ContentEmbedding(values=vectors[0] if vectors else [])
            ).model_dump()
        )
        # Booked as the model that gave the vectors, which for a fallback is not the one named.
        acct.embedded(trail.served_model, units=embed_request.size, vectors=vectors)
    return JSONResponse(payload, headers=headers)


async def _stream_response(
    request: Request,
    asked: CanonicalRequest,
    prepared: Prepared,
    trail: AuditTrail,
    *,
    sse: bool,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Stream chunks as SSE (`?alt=sse`, the google-genai SDK) or as a JSON array (Gemini REST).

    Opens the chain **before** the response exists (`FRD-302` FR-6): the candidates are tried in
    order until one has produced a chunk, which is both what applies the dispatch conditions and
    what makes a refusal a status rather than a stream that stops. The model that answered is the
    one recorded and billed — for a fallback that is not the model the caller named.

    A failure after the status went out is sent the way Google sends one: an error element, the
    last event of the SSE stream or the last element of the array. The google-genai SDK raises it
    as an `APIError`; a stream that merely stopped would read as a complete answer.

    **The status waits for the chain no longer than `AIRA_STREAM_KEEPALIVE_SECONDS`**
    (`ADR-0046`). A model that reads a large context or thinks first produces nothing for minutes,
    and a proxy or a client that sees no byte for that long closes the connection. So the response
    then begins anyway, the chain goes on looking for its first chunk — falling back as before —
    and the stream sends `KEEPALIVE` whenever it has been quiet for that long, before the first
    chunk and after it. A refusal that comes after that is the stream's last element, like a
    failure after the first chunk.
    """
    quiet = keepalive_seconds(request)
    # Before the chain is opened: the wait for a candidate's first chunk is time this request
    # took, and a `started` taken after it would report every stream as faster than it was.
    started = time.monotonic()
    # The caller's request, fitted to each candidate in turn (`fitting`), as on `generateContent`.
    opening = Opening(
        stream_with_fallback(
            providers_of(request),
            asked,
            prepared.fallbacks,
            permits=await requirements_for(request, asked),
            routing_of=await declared_routing(request),
            fit=fitting(request),
        )
    )
    stream = StreamAccounting(
        request,
        trail,
        prepared,
        await opening.within(quiet) or opening,
        api="gemini",
        operation="streamGenerateContent",
        started=started,
        shows_reasoning=True,
    )

    async def generate_chunks() -> AsyncGenerator[str]:
        async with stream.accounted():
            separator = ""
            if not sse:
                yield "["
            try:
                # The first chunk is in hand, or on its way; its own model call is marked where
                # the request was issued, inside the chain.
                async for chunk in with_pings(stream.chunks, quiet):
                    if chunk is TICK:
                        yield KEEPALIVE
                        continue
                    assert isinstance(chunk, CanonicalChunk)
                    payload = chunk_to_gemini(stream.take(chunk), stream.model).model_dump_json(
                        exclude_none=True
                    )
                    if sse:
                        yield f"data: {payload}\n\n"
                    else:
                        yield f"{separator}{payload}"
                        separator = ","
            except REFUSALS as exc:
                stream.broke_off(exc)
                # The same envelope the refusal boundary renders, as the stream's last element.
                element = bytes(refusal_response(exc, request).body).decode()
                yield f"data: {element}\n\n" if sse else f"{separator}{element}"
            if not sse:
                yield "]"
            stream.delivered()

    media_type = "text/event-stream" if sse else "application/json"
    return AccountedStream(
        stream,
        generate_chunks(),
        media_type=media_type,
        headers={**(headers or {}), **STREAM_HEADERS},
    )


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


def _asked_for_reasoning(parsed: schemas.GenerateContentRequest | None) -> bool | None:
    """This surface's spelling of "give me the model's reasoning" (`FRD-135` FR-4): ``True``,
    ``False`` when declined explicitly, ``None`` when the caller did not say."""
    if parsed is None or parsed.generationConfig is None:
        return None
    thinking = parsed.generationConfig.thinkingConfig
    return None if thinking is None else thinking.includeThoughts
