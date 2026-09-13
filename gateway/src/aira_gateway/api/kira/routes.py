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
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from aira_common.models import Capability
from aira_gateway.api.kira import BASE, errors, schemas
from aira_gateway.api.kira.attribution import resolve as resolve_attribution
from aira_gateway.api.kira.headers import note_unmodelled, surface_headers
from aira_gateway.api.kira.mapping import (
    completed_event,
    to_canonical,
    to_chat_response,
    to_embedding,
    update_event,
)
from aira_gateway.api.kira.refusals import KIRA_REFUSALS, refusal_response
from aira_gateway.api.serving import (
    Prepared,
    StreamedNotice,
    accounting,
    annotate,
    catalog_of,
    check_structured_result,
    declared_routing,
    ensure_body_is_encodable,
    json_body,
    prepare_for_dispatch,
    record_refusal,
    refusal_outcome,
    requirements_for,
    resolve_direct_target,
    schema_bounds,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS, AmbiguousModelId
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.embedding import (
    DEFAULT_TASK_TYPE,
    EMBEDDING_AGGREGATION_NOT_SUPPORTED,
    EmbeddingRejected,
)
from aira_gateway.pipeline.dispatch import dispatch_with_fallback
from aira_gateway.state import providers_of
from aira_gateway.telemetry import model_call_chunks, model_call_span

router = APIRouter(tags=["kira"], prefix=BASE)

_log = structlog.get_logger(__name__)

#: How much of a refused body goes into the log line: a generous conversation, a negligible log.
REFUSAL_BODY_LIMIT = 12 * 1024


# == routes =======================================================================================


@router.post("/chat")
async def chat(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="chat", api="kira")
    try:
        prepared = await _prepare(request, principal, trail)
        canonical = prepared.canonical
        assert canonical is not None
        async with accounting(
            request, trail, prepared, api="kira", operation="chat", started=started
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
            check_structured_result(canonical, answer)
            payload = to_chat_response(answer).model_dump()
            acct.served(answer.model, answer.usage, payload)
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
    out, so the conditions a candidate must meet are checked before the response exists
    (`resolve_direct_target`), where a refusal can still be a status.
    """
    started = time.monotonic()
    trail = AuditTrail(operation="streaming-chat", api="kira")
    try:
        prepared = await _prepare(request, principal, trail)
        canonical = prepared.canonical
        assert canonical is not None
        provider = await resolve_direct_target(request, canonical.model, canonical)
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, started=started)

    async def events() -> AsyncIterator[str]:
        async with accounting(
            request, trail, prepared, api="kira", operation="streaming-chat", started=started
        ) as acct:
            parts: list[str] = []
            usage: CanonicalUsage | None = None
            finish_reason = "stop"
            # Accumulated as sent, so the terminal event carries exactly what was streamed.
            notice = StreamedNotice(
                prepared.notices, structured=canonical.response_schema is not None
            )
            # Whether the stream ended on its own — answered, or stopped by a refusal.
            finished = False
            try:
                async for chunk in model_call_chunks(
                    canonical.model, provider.stream_generate(canonical)
                ):
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if not chunk.text_delta:
                        # No event without text: an empty one is the synthetic heartbeat `FRD-111`
                        # §5.4 refused.
                        continue
                    led = notice.lead(chunk.text_delta)
                    parts.append(led)
                    yield f"data: {json.dumps(update_event(led))}\n\n"
                finished = True
            except KIRA_REFUSALS as exc:
                # The status is already sent; the failure is reported into the accounting.
                finished = True
                acct.failed(502, refusal_outcome(exc))
                return
            finally:
                if not finished and usage is not None:
                    # The caller left mid-answer after the upstream reported usage: that much was
                    # spent and reached them, so it is settled — and recorded as `499`.
                    acct.abandoned(canonical.model, usage, {"text": "".join(parts)})
                outcome_note = notice.outcome()
                if outcome_note is not None:
                    trail.decisions.append(outcome_note)

            answer = CanonicalResponse(
                model=canonical.model,
                text="".join(parts),
                usage=usage or CanonicalUsage(prompt_tokens=0, completion_tokens=0),
                finish_reason=finish_reason,
            )
            try:
                # An unfinished schema-constrained answer is not data (`FRD-112` FR-6). The status
                # is already sent, so the terminal event is withheld instead: a client waiting for
                # `completed` never treats half a document as whole.
                check_structured_result(canonical, answer)
            except KIRA_REFUSALS as exc:
                acct.failed(502, refusal_outcome(exc))
                return
            trail.served_by(answer.model, 0)
            event = completed_event(answer)
            acct.served(answer.model, usage, event["data"])
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers=surface_headers(request)
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

        # This surface owns only the predecessor's default task type; what may be embedded, and
        # the order of the controls around it, belong to the shared sequence.
        prepared = await prepare_for_dispatch(
            request,
            trail,
            method="embedContent",
            embed=to_embedding(parsed, model),
            default_task_type=DEFAULT_TASK_TYPE,
        )
        embed_request = prepared.embed
        assert embed_request is not None

        # Aggregation is asked here, where `parsed.text` still says whether a list was sent: the
        # mapper joins a list into one text, so the shared batch check can no longer see it.
        if (
            isinstance(parsed.text, list)
            and len(parsed.text) > 1
            and not prepared.declaration.supports_batch
        ):
            raise EmbeddingRejected(
                EMBEDDING_AGGREGATION_NOT_SUPPORTED,
                f"Model '{prepared.declaration.name}' does not accept a list of texts. Send them "
                "one at a time, or use a model whose catalog entry declares batch support.",
            )
        async with accounting(
            request, trail, prepared, api="kira", operation="embed", started=started
        ) as acct:
            # An embedding has no dispatch chain, so the conditions are applied here.
            provider = await resolve_direct_target(request, model)
            with model_call_span(str(embed_request.model), purpose="embed"):
                vectors = await provider.embed(embed_request)
            # One vector, whatever the input shape: a list was joined into one text.
            payload = schemas.EmbeddingResponse(vector=vectors[0] if vectors else []).model_dump()
            acct.embedded(model, payload, units=embed_request.size, vectors=vectors)
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
    note_unmodelled(request, parsed)
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
        raise errors.KiraError(422, errors.INVALID_MAX_TOKENS, "maxTokens must be positive.")
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
    """Render a refusal in this surface's envelope, log it, and record it."""
    response = _error_response(request, exc)
    # The refused body goes into the log as well as the audit row: diagnosing a client's 422 must
    # not need a database query against a use case that may not store payloads. Truncated rather
    # than dropped — the first 12 KB of a large body name its fields.
    encoded = json.dumps(trail.body, default=str) if trail.body else ""
    _log.warning(
        "kira_request_refused",
        operation=trail.operation,
        status=response.status_code,
        error=type(exc).__name__,
        model=trail.served_model or None,
        unmodelled_fields=list(getattr(request.state, "unmodelled", ())),
        request_body=encoded[:REFUSAL_BODY_LIMIT],
        request_body_truncated=len(encoded) > REFUSAL_BODY_LIMIT,
    )
    await record_refusal(request, trail, exc, status=response.status_code, started=started)
    return response


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    response = refusal_response(exc)
    response.headers.update(surface_headers(request))
    return response
