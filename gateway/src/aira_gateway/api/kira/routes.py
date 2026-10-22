"""The predecessor's model endpoints — chat, streaming chat, embed — served by AIRA (`FRD-107`).

Only the predecessor's wire format lives here; everything below it — pre-dispatch controls,
pipeline, dispatch chain, audit writer — is the shared `api.serving` layer. The informational
endpoints (models, health, version, usage) are in `info_routes`.

Where a model cannot honour a field, the request is refused rather than served differently: a
field accepted and quietly ignored produces an answer that is wrong for a reason the caller cannot
see.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from aira_common.models import Capability
from aira_gateway.api.kira import BASE, errors, schemas
from aira_gateway.api.kira.attribution import resolve as resolve_attribution
from aira_gateway.api.kira.embedding_prompts import is_prompted
from aira_gateway.api.kira.headers import note_unmodelled, surface_headers
from aira_gateway.api.kira.keepalive import PING, STREAM_HEADERS, TICK, with_pings
from aira_gateway.api.kira.mapping import (
    check_answered,
    completed_event,
    function_call_event,
    stored,
    to_canonical,
    to_chat_response,
    to_embedding,
    to_rerank,
    to_rerank_response,
    update_event,
)
from aira_gateway.api.kira.refusals import KIRA_REFUSALS, refusal_response
from aira_gateway.api.serving import (
    AccountedStream,
    Prepared,
    StreamAccounting,
    accounting,
    annotate,
    catalog_of,
    check_structured_result,
    declared_routing,
    ensure_body_is_encodable,
    fitting,
    json_body,
    prepare_for_dispatch,
    rank,
    record_refusal,
    refusal_reason,
    requirements_for,
    resolve_direct_target,
    schema_bounds,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS, AmbiguousModelId
from aira_gateway.core.canonical import CanonicalChunk, CanonicalResponse, CanonicalUsage
from aira_gateway.embedding import (
    DEFAULT_TASK_TYPE,
    EMBEDDING_AGGREGATION_NOT_SUPPORTED,
    EmbeddingRejected,
)
from aira_gateway.explaining import REASON_HEADER, explain
from aira_gateway.pipeline.dispatch import dispatch_with_fallback, stream_with_fallback
from aira_gateway.state import providers_of
from aira_gateway.telemetry import model_call_span

router = APIRouter(tags=["kira"], prefix=BASE)

_log = structlog.get_logger(__name__)

#: How many fields, list entries and parts a refused body's logged *shape* names. The shape grows
#: with the body, which the caller writes, so it is bounded here; past it the rest is counted.
SHAPE_ENTRIES = 256
#: A field name is the caller's string too, and a name longer than this is not one anybody needs.
SHAPE_NAME_LENGTH = 64
#: How many of a validation failure's field paths the refusal's log line names.
INVALID_FIELDS_LOGGED = 32


# == routes =======================================================================================


@router.post("/chat")
async def chat(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="chat", api="kira")
    try:
        prepared = await _prepare(request, principal, trail)
        canonical, asked = prepared.canonical, prepared.asked
        assert canonical is not None and asked is not None
        async with accounting(
            request, trail, prepared, api="kira", operation="chat", started=started
        ) as acct:
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
            check_answered(answer)
            payload = to_chat_response(answer).model_dump()
            acct.served(
                answer.model,
                answer.usage,
                stored(payload),
                [call.name for call in answer.tool_calls],
            )
        return JSONResponse(payload, headers=surface_headers(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, started=started)


@router.post("/streaming-chat")
async def streaming_chat(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    """The predecessor's SSE contract: an ``update`` event per text chunk, then ``completed``.

    ``completed`` still carries the whole answer and the usage, so a client that reads only the
    terminal event is unaffected by the updates. A stream cannot fall back once its first chunk is
    out — but it can until then, so the chain is **opened** before the response exists
    (`stream_with_fallback`, `FRD-302` FR-6): the candidates meet every condition there, and a
    chain with nothing left to offer is still a status rather than a stream that stops.
    """
    started = time.monotonic()
    trail = AuditTrail(operation="streaming-chat", api="kira")
    try:
        prepared = await _prepare(request, principal, trail)
        asked = prepared.asked
        assert asked is not None
        streamed = await stream_with_fallback(
            providers_of(request),
            asked,
            prepared.fallbacks,
            permits=await requirements_for(request, asked),
            routing_of=await declared_routing(request),
            fit=fitting(request),
        )
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, started=started)

    stream = StreamAccounting(
        request,
        trail,
        prepared,
        streamed,
        api="kira",
        operation="streaming-chat",
        started=started,
    )
    served = streamed.model

    async def events() -> AsyncGenerator[str]:
        async with stream.accounted():
            try:
                # The first chunk is already in hand; the chain marked its model call where the
                # request was issued. A quiet model is a comment every fifteen seconds, never an
                # event (`keepalive`).
                async for chunk in with_pings(stream.chunks):
                    if chunk is TICK:
                        yield PING
                        continue
                    assert isinstance(chunk, CanonicalChunk)
                    led = stream.take(chunk).text_delta
                    if not led or asked.structured:
                        # No event without text: an empty one is the synthetic heartbeat `FRD-111`
                        # §5.4 refused. And none for a document: a fragment of JSON is not a
                        # message, and the predecessor sends a constrained answer whole, in
                        # `completed` alone.
                        continue
                    yield f"data: {json.dumps(update_event(led))}\n\n"
            except KIRA_REFUSALS as exc:
                # The status is already sent: the stream ends without `completed`, which is how a
                # client of this contract learns that the answer is not whole.
                stream.broke_off(exc)
                return

            # Built from what was streamed, so the terminal event carries exactly that.
            answer = CanonicalResponse(
                model=served,
                text=stream.text,
                usage=stream.usage or CanonicalUsage(prompt_tokens=0, completion_tokens=0),
                finish_reason=stream.finish_reason or "stop",
                tool_calls=stream.calls,
            )
            try:
                # An unfinished schema-constrained answer is not data (`FRD-112` FR-6). The status
                # is already sent, so the terminal event is withheld instead: a client waiting for
                # `completed` never treats half a document as whole.
                check_structured_result(asked, answer)
                check_answered(answer)
            except KIRA_REFUSALS as exc:
                stream.broke_off(exc)
                return
            # One event per call after the text, then `completed`, which carries them too
            # (`FRD-146` FR-5): a client that reads only the terminal event still sees them.
            for call in answer.tool_calls:
                yield f"data: {json.dumps(function_call_event(call))}\n\n"
            event = completed_event(answer)
            stream.delivered(stored(event["data"]))
            yield f"data: {json.dumps(event)}\n\n"

    return AccountedStream(
        stream,
        events(),
        media_type="text/event-stream",
        headers={**surface_headers(request), **STREAM_HEADERS},
    )


@router.post("/embed")
async def embed(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="embed", api="kira")
    try:
        body = await _attributed_body(request, principal, trail)
        parsed = _parse(schemas.EmbeddingRequest, body)
        note_unmodelled(request, parsed)
        model = await _resolve_model(request, parsed.model_id)
        trail.requested_model = model
        declaration = await catalog_of(request).declaration(model)
        embedding = to_embedding(parsed, model, prompted=is_prompted(declaration))

        # Aggregation is asked here, where `parsed.text` still says whether a list was sent: the
        # mapper joins a list into one text, so the shared batch check can no longer see it. And
        # before the shared sequence, which spends the caller's allowance on its way — a request
        # that can never be served must not (`prepare_for_dispatch`). A model that cannot embed at
        # all is left to that sequence, which names that instead.
        if (
            isinstance(parsed.text, list)
            and len(parsed.text) > 1
            and declaration.can(Capability.EMBED)
            and not declaration.supports_batch
        ):
            raise EmbeddingRejected(
                EMBEDDING_AGGREGATION_NOT_SUPPORTED,
                f"Model '{declaration.name}' does not accept a list of texts. Send them one at a "
                "time, or use a model whose catalog entry declares batch support.",
            )

        # This surface owns only the predecessor's default task type; what may be embedded, and
        # the order of the controls around it, belong to the shared sequence.
        prepared = await prepare_for_dispatch(
            request,
            trail,
            method="embedContent",
            embed=embedding,
            default_task_type=DEFAULT_TASK_TYPE,
        )
        embed_request = prepared.embed
        assert embed_request is not None
        async with accounting(
            request, trail, prepared, api="kira", operation="embed", started=started
        ) as acct:
            # An embedding has no dispatch chain, so the conditions are applied here.
            provider = await resolve_direct_target(request, model)
            with model_call_span(str(embed_request.model), purpose="embed"):
                vectors = await provider.embed(embed_request)
            # One vector, whatever the input shape: a list was joined into one text.
            payload = schemas.EmbeddingResponse(vector=vectors[0] if vectors else []).model_dump()
            acct.embedded(model, units=embed_request.size, vectors=vectors)
        return JSONResponse(payload, headers=surface_headers(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, started=started)


@router.post("/rerank")
async def rerank(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    """Order records against a query (`FRD-147`). Parsed here; everything else is the shared
    sequence's, as for every other verb — attribution, release, rate limit, pipeline, budget."""
    started = time.monotonic()
    trail = AuditTrail(operation="rerank", api="kira")
    try:
        body = await _attributed_body(request, principal, trail)
        parsed = _parse(schemas.RerankRequest, body)
        note_unmodelled(request, parsed)
        model = await _resolve_model(request, parsed.model_id)
        trail.requested_model = model
        prepared = await prepare_for_dispatch(
            request, trail, method="rerank", rerank=to_rerank(parsed, model)
        )
        assert prepared.rerank is not None
        async with accounting(
            request, trail, prepared, api="kira", operation="rerank", started=started
        ) as acct:
            ranked = await rank(request, prepared)
            payload = to_rerank_response(ranked).model_dump()
            acct.ranked(model, units=prepared.rerank.units, records=ranked)
        return JSONResponse(payload, headers=surface_headers(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, started=started)


# == parsing ======================================================================================


async def _prepare(request: Request, principal: Principal, trail: AuditTrail) -> Prepared:
    """This surface's part of the way to dispatch, then the shared sequence.

    Attribute the caller and read the body, parse the predecessor's shape, turn the numeric model
    id into a model and refuse what this contract does not serve — then `prepare_for_dispatch`,
    which owns the order of everything after.
    """
    body = await _attributed_body(request, principal, trail)
    parsed = _parse(schemas.ChatRequest, body)
    note_unmodelled(request, parsed, ignored=schemas.ignored_values(parsed))
    model = await _resolve_model(request, parsed.model_id)
    trail.requested_model = model
    await _check_chat(request, model, parsed.max_tokens)

    canonical = to_canonical(parsed, model, bounds=schema_bounds(request))
    prepared = await prepare_for_dispatch(
        request,
        trail,
        method="generateContent",
        canonical=canonical,
        requested_output=parsed.max_tokens,
    )
    assert prepared.canonical is not None
    return prepared


async def _attributed_body(
    request: Request, principal: Principal, trail: AuditTrail
) -> dict[str, Any]:
    """Resolve who is calling, **then** read the body.

    Attribution needs only the header and the principal, and it has to come first: a refusal is
    recorded only for an attributed request, so a malformed body from a valid credential would
    otherwise leave no audit row (`FRD-122`).
    """
    resolve_attribution(request, principal)
    body = await _json(request)
    trail.body = body
    return body


async def _json(request: Request) -> dict[str, Any]:
    """The body as a JSON object, or the predecessor's answer for one that is not.

    `422 VALIDATION_ERROR` rather than `400`: semantically worse, but it is what the predecessor
    answers — its malformed JSON reaches FastAPI's validation handler — and a migrating client
    switches on the code.
    """
    try:
        body = await json_body(request)
    except ValueError as exc:
        raise errors.KiraError(
            422, errors.VALIDATION_ERROR, "Request body is not valid JSON."
        ) from exc
    # A surface parses, and text that cannot be written as UTF-8 is a parsing question.
    ensure_body_is_encodable(body)
    if not isinstance(body, dict):
        raise errors.KiraError(422, errors.VALIDATION_ERROR, "Request body must be an object.")
    return body


def _parse[Model: BaseModel](schema: type[Model], body: dict[str, Any]) -> Model:
    """The body as ``schema``, or `422 VALIDATION_ERROR` naming what is wrong."""
    try:
        return schema.model_validate(body)
    except ValidationError as exc:
        raise errors.KiraError(
            422,
            errors.VALIDATION_ERROR,
            "Request validation failed.",
            errors.validation_details(exc),
        ) from exc


async def _resolve_model(request: Request, model_id: int) -> str:
    """The catalogue name for the predecessor's numeric model id."""
    if model_id not in schemas.MODEL_IDS:
        raise errors.KiraError(
            422,
            errors.MODEL_NOT_FOUND,
            f"No model with id {model_id}. Ids are assigned in the model catalog.",
        )
    try:
        name = await catalog_of(request).by_numeric_id(model_id)
    except AmbiguousModelId as exc:
        # 503: the installation is misconfigured and the caller did nothing wrong. Picking one of
        # the two would answer, bill and audit under a model they never named. Which models
        # collide is logged by the catalogue, never disclosed to the caller.
        raise errors.KiraError(
            503,
            errors.MODEL_NOT_FOUND,
            f"Model id {model_id} is not uniquely assigned in this installation's catalog. "
            "An administrator has to resolve it.",
        ) from exc
    if name is None:
        # The contract's own status: a generated client reads 422 as "wrong field", 404 as
        # "wrong URL", and only the first sends anybody to look at `model_id`.
        raise errors.KiraError(
            422,
            errors.MODEL_NOT_FOUND,
            f"No model with id {model_id}. Ids are assigned in the model catalog.",
        )
    return name


async def _check_chat(request: Request, model: str, max_tokens: int | None) -> None:
    """The catalogue's rules for a chat request, in this contract's own codes.

    `check_declaration` enforces the same limits on both surfaces; they are restated here only to
    choose the words, because a migrating client switches on `MAX_TOKENS_EXCEEDS_CAP` rather than
    on a generic validation error. Both read the same figures, so neither can become the only check.
    """
    declaration = await catalog_of(request).declaration(model)
    if not declaration.can(Capability.GENERATE):
        raise errors.KiraError(
            422, errors.NO_CHAT_CAPABILITIES, f"Model '{model}' does not support chat."
        )
    if max_tokens is not None and max_tokens <= 0:
        # The predecessor's field constraint, so its generic code (`FRD-146` FR-15).
        raise errors.KiraError(422, errors.VALIDATION_ERROR, "maxTokens must be positive.")
    cap = declaration.max_output_tokens
    if max_tokens is not None and cap is not None and max_tokens > cap:
        raise errors.KiraError(
            422,
            errors.MAX_TOKENS_EXCEEDS_CAP,
            f"maxTokens {max_tokens} exceeds the {cap} this model accepts.",
        )
    if max_tokens is not None and max_tokens > MAX_ACCOUNTABLE_TOKENS:
        raise errors.KiraError(
            422,
            errors.MAX_TOKENS_EXCEEDS_CAP,
            f"maxTokens {max_tokens} exceeds the {MAX_ACCOUNTABLE_TOKENS} this gateway "
            "can account for.",
        )


# == refusals =====================================================================================


async def _refused(
    request: Request, trail: AuditTrail, exc: Exception, *, started: float
) -> JSONResponse:
    """Render a refusal in this surface's envelope, log it, and record it.

    The log line names what the body *was* — its fields, each value's kind and size, and the paths
    a validation failure names — so a client's 422 can be diagnosed without a database query. Never
    what it *said*: a log line is outside every control on stored content (a use case's
    `store_payloads`, redaction, the retention clock, the recorded read — `ADR-0016`), and the
    privacy notice promises that no log line carries the content of a request (`FRD-625`). The
    content itself is in the audit row, where those controls apply.
    """
    response = _error_response(request, exc)
    details = exc.details if isinstance(exc, errors.KiraError) and exc.details else []
    _log.warning(
        "kira_request_refused",
        operation=trail.operation,
        status=response.status_code,
        error=type(exc).__name__,
        model=trail.served_model or None,
        unmodelled_fields=list(getattr(request.state, "unmodelled", ())),
        request_shape=body_shape(trail.body) if trail.body else None,
        # Without the envelope's leading `body`: every path here is one into the body.
        invalid_fields=[".".join(detail["loc"][1:]) for detail in details[:INVALID_FIELDS_LOGGED]],
    )
    await record_refusal(request, trail, exc, status=response.status_code, started=started)
    return response


def body_shape(body: Any) -> Any:
    """``body`` with every value replaced by its kind and size: ``{"text": "str(27)"}``.

    Field names stay, since they are the contract's and name what a refusal is about; strings
    become their length, numbers, booleans and nulls their kind. Bounded by `SHAPE_ENTRIES`, so a
    body of a hundred thousand entries does not become a log line of the same size.
    """
    remaining = SHAPE_ENTRIES

    def walk(value: Any) -> Any:
        nonlocal remaining
        if isinstance(value, dict):
            fields: dict[str, Any] = {}
            for index, (name, item) in enumerate(value.items()):
                if remaining <= 0:
                    fields["…"] = f"{len(value) - index} more"
                    break
                remaining -= 1
                fields[str(name)[:SHAPE_NAME_LENGTH]] = walk(item)
            return fields
        if isinstance(value, list):
            entries: list[Any] = []
            for index, item in enumerate(value):
                if remaining <= 0:
                    entries.append(f"… {len(value) - index} more")
                    break
                remaining -= 1
                entries.append(walk(item))
            return entries
        if isinstance(value, str):
            return f"str({len(value)})"
        if isinstance(value, bool):
            return "bool"
        return "null" if value is None else "number"

    return walk(body)


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    response = refusal_response(exc)
    response.headers.update(surface_headers(request))
    # Which control refused, in a header: KIRA's envelope is the predecessor's and keeps its fields
    # (`FRD-628` FR-3), so a migrating client's error handling is untouched.
    explain(request.state, REASON_HEADER, refusal_reason(exc))
    return response
