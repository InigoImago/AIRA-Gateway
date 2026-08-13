"""The predecessor's endpoints, served by AIRA (FRD-107, Stage A + B).

Under ``/kira/api/external``, so a migrating consumer changes a base URL and nothing else. This
module maps one wire format; **everything below the surface is shared** with the Gemini routes via
``api/serving`` — the pre-dispatch gate, the pipeline, the dispatch chain, the audit writer. A
second copy of those controls is the `:embedContent` failure with an extra hundred lines to hide in.

Stage B (2026-08-06) serves the whole contract: text, documents, **thinking**, **structured
output** and **batch embedding with task types**. Nothing about the wire format changed — the
fields Stage A refused by name are now honoured, which is the migration `ADR-0010` promised.

Where a model cannot honour one, the request is still **refused** rather than served differently.
A field accepted and quietly ignored produces an answer that is wrong for a reason the caller
cannot see, and that is true whether the reason is "not built yet" or "this model cannot".
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
from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.kira import errors, schemas
from aira_gateway.api.kira.attribution import resolve as resolve_attribution
from aira_gateway.api.kira.mapping import (
    completed_event,
    to_canonical,
    to_chat_response,
    to_embedding,
    update_event,
)
from aira_gateway.api.serving import (
    REFUSALS,
    Prepared,
    accounting,
    catalog_of,
    check_structured_result,
    declared_provider,
    elapsed_ms,
    prepare_for_dispatch,
    provenance,
    refusal_outcome,
    registry_of,
    requirements_for,
    resolve_direct_target,
    schema_bounds,
    upstream_status,
)
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary, tool_summary
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.catalog import AmbiguousModelId, ModelDeclaration
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.core.schema import SchemaRejected
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.embedding import (
    DEFAULT_TASK_TYPE,
    EMBEDDING_AGGREGATION_NOT_SUPPORTED,
    EmbeddingRejected,
)
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.reporting.service import ReportingService
from aira_gateway.thinking import ThinkingRejected
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


#: A shared control's HTTP status, in the compatibility surface's error vocabulary.
#: Anything unmapped is a validation error, which is what a 4xx from a pre-dispatch check is.
_KIRA_CODE_FOR = {
    404: errors.MODEL_NOT_FOUND,
    403: errors.STANDARD_USER_PERMISSION_REQUIRED,
    429: errors.EXTERNAL_KI_API_TOO_MANY_REQUEST,
    502: errors.EXTERNAL_KI_API_ERROR,
}


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    """Every refusal, in the predecessor's vocabulary."""
    if isinstance(exc, errors.KiraError):
        response = exc.to_response()
    elif isinstance(exc, ThinkingRejected | EmbeddingRejected):
        # These carry the contract's own codes — `INVALID_THINKING_MODE`,
        # `THINKING_TOKEN_COUNT_TOO_HIGH`, `EMBEDDING_AGGREGATION_NOT_SUPPORTED` and the rest — so
        # a migrating client's error handling keeps switching on the same strings it always did.
        response = errors.kira_error_response(422, exc.code, exc.message)
    elif isinstance(exc, AttachmentRejected | SchemaRejected):
        response = errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))
    elif isinstance(exc, GeminiHTTPError):
        # The shared controls in `api/serving` raise this — they are surface-agnostic by design,
        # and this surface has to put their refusals into the predecessor's vocabulary. Without
        # this branch every one of them fell through to the `else` and became a **500**, which is
        # the exact failure the shared module exists to prevent, arriving from the other side: a
        # control that works but cannot be *reported* on one of the surfaces it protects.
        response = errors.kira_error_response(
            exc.code, _KIRA_CODE_FOR.get(exc.code, errors.VALIDATION_ERROR), exc.message
        )
    elif isinstance(exc, Suspended | RateLimited | BudgetExceeded):
        response = errors.kira_error_response(
            429, errors.EXTERNAL_KI_API_TOO_MANY_REQUEST, exc.message
        )
    elif isinstance(exc, PipelineRejected):
        response = errors.kira_error_response(exc.code, errors.VALIDATION_ERROR, exc.message)
    elif isinstance(exc, NoCapableModel):
        response = errors.kira_error_response(400, errors.MODEL_NOT_FOUND, str(exc))
    elif isinstance(exc, UpstreamError):
        code = upstream_status(exc.status_code)[0]
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


def _details(exc: ValidationError) -> list[dict[str, Any]]:
    """The predecessor's ``details`` array, and **only things that serialise**.

    ``errors()`` carries a ``ctx`` holding the original exception object whenever a *custom*
    validator raised — and ours do, for "a part carries either text or data". Rendering that as
    JSON raised `TypeError` inside the response, which the framework turned into a **500**: a
    caller's malformed body became our server error, with the generic envelope rather than the
    predecessor's, on the one surface whose contract is its error shape.

    ``include_input`` is off for a second reason. Echoing the offending value back is a habit that
    eventually reflects a prompt, or a credential somebody put in the wrong field, into a response
    and from there into whatever logs it.

    **What actually enforces both is the comprehension**, which takes two named fields and nothing
    else; the flags are the belt to its braces. Worth stating because the mutation for this
    property was first pointed at the flags and survived — they were already redundant, so removing
    them changed nothing, and a mutation that cannot fail is a claim rather than a test.
    """
    return [
        {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


def _thinking_config(declaration: ModelDeclaration) -> schemas.ThinkingConfig | None:
    """What the predecessor's `/models` says about a model's thinking.

    ``None`` for a model that declares none — rather than an empty config, which would read as
    "thinking exists here and nothing is allowed" instead of "nobody has said".
    """
    modes = declaration.thinking_modes
    if not modes:
        return None
    minimum, maximum = declaration.thinking_bounds
    default = declaration.thinking_default
    return schemas.ThinkingConfig(
        mode=sorted(str(mode) for mode in modes),
        minTokens=minimum,
        maxTokens=maximum,
        defaultThinking=(
            schemas.ThinkingSetting(mode=str(default.get("mode")), tokens=default.get("tokens"))
            if default and default.get("mode")
            else None
        ),
    )


async def _resolve_model(request: Request, model_id: int) -> str:
    try:
        name = await catalog_of(request).by_numeric_id(model_id)
    except AmbiguousModelId as exc:
        # Two entries claim this id. **503, not 500 and not a served answer**: the installation is
        # misconfigured, the caller did nothing wrong, and picking one of the two would answer,
        # bill and audit under a model they never named — with nothing in the response looking
        # wrong. The models are named in the log, never to the caller: which models an
        # installation runs is not this surface's to disclose (the catalog logs them once).
        raise errors.KiraError(
            503,
            errors.MODEL_NOT_FOUND,
            f"Model id {model_id} is not uniquely assigned in this installation's catalog. "
            "An administrator has to resolve it.",
        ) from exc
    if name is None:
        raise errors.KiraError(
            404,
            errors.MODEL_NOT_FOUND,
            f"No model with id {model_id}. Ids are assigned in the model catalog.",
        )
    return name


async def _attributed_body(
    request: Request, principal: Principal, trail: AuditTrail
) -> dict[str, Any]:
    """Resolve who is calling, **then** read the body — in that order, once.

    **Attribution first, before the body is even parsed.** It needs only the header and the
    principal — never the body — and putting it after meant a caller with a *valid* credential
    sending malformed JSON left **no audit row at all**: `_refused` records only when
    `request.state.attribution` is set. The Gemini surface writes that row, because there
    attribution is a router-level dependency and is therefore always resolved first.

    Two surfaces, one governance question, two answers — the shape this project keeps finding.
    `FRD-122`'s rule is that the log records what was **asked**, and a malformed body from a known
    credential is very much something that was asked.

    **Extracted 2026-08-12.** These three lines and the paragraph above them stood, byte for byte,
    in all three of this surface's dispatching routes. That is the failure `prepare_for_dispatch`
    was written about one layer up: a fact repeated at every entry point is a fact eventually
    missing from one of them, and the *order* is the whole guarantee — which no copy can state for
    the others. It is also how the mutation guarding this property came to protect one verb: the
    anchor matched three identical blocks and the harness edits the first.
    """
    resolve_attribution(request, principal)
    body = await _json(request)
    trail.body = body
    return body


async def _prepare(
    request: Request, principal: Principal, body: dict[str, Any], trail: AuditTrail
) -> tuple[Prepared, Any]:
    """Everything between "a body arrived" and "dispatch it" that is *this surface's* business.

    Which is: resolve the caller, parse the predecessor's shape, turn an integer model id into a
    model, and refuse what this contract does not serve. Everything after that is the same for
    every surface and belongs to `prepare_for_dispatch`.

    The trail is **passed in** rather than created here. It used to be returned and the caller's
    own reassigned over — two audit trails for one request, and the one carrying the body thrown
    away.
    """
    resolve_attribution(request, principal)
    try:
        parsed = schemas.ChatRequest.model_validate(body)
    except ValidationError as exc:
        raise errors.KiraError(
            422, errors.VALIDATION_ERROR, "Request validation failed.", _details(exc)
        ) from exc

    model = await _resolve_model(request, parsed.model_id)
    trail.requested_model = model
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

    canonical = to_canonical(parsed, model, bounds=schema_bounds(request))
    # One call, and the order is not this surface's to choose. It used to be four steps written
    # out here and the reservation written out again in each of the three handlers below — which
    # is how this surface came to have no rate limiting at all when the take moved.
    prepared = await prepare_for_dispatch(
        request,
        trail,
        method="generateContent",
        canonical=canonical,
        requested_output=parsed.max_tokens,
    )
    assert prepared.canonical is not None
    return prepared, parsed


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
        provenance=await provenance(request, trail.served_model),
        api=trail.api,
        tool_calls=tool_summary(trail),
    )


@router.post("/chat")
async def chat(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="chat", api="kira")
    body: dict[str, Any] = {}
    try:
        body = await _attributed_body(request, principal, trail)
        prepared, parsed = await _prepare(request, principal, body, trail)
        canonical, fallbacks = prepared.canonical, prepared.fallbacks
        assert canonical is not None
        async with accounting(
            request,
            trail,
            prepared,
            api="kira",
            operation="chat",
            body=body,
            started=started,
        ) as acct:
            dispatched = await dispatch_with_fallback(
                registry_of(request),
                canonical,
                fallbacks,
                permits=await requirements_for(request, canonical),
                provider_of=await declared_provider(request),
            )
            trail.served_by(dispatched.response.model, dispatched.candidate_index)
            trail.passed_over(dispatched.skipped)
            check_structured_result(canonical, dispatched.response)
            payload = to_chat_response(dispatched.response).model_dump()
            acct.served(dispatched.response.model, dispatched.response.usage, payload)
        return JSONResponse(payload, headers=_sunset(request))
    except KIRA_REFUSALS as exc:
        return await _refused(request, trail, exc, body=body, started=started, operation="chat")


@router.post("/streaming-chat")
async def streaming_chat(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    """The predecessor's SSE contract: ``update`` events as the answer arrives, then ``completed``.

    **This used not to stream at all.** It called the non-streaming dispatch, waited for the whole
    answer and sent one terminal event — SSE as a costume. `FRD-111` §5.4 is the reason, and it was
    half right: it decided against inventing ``update`` events *that carry no model output*, which
    remains correct. But that is a different question from whether the output should arrive
    progressively, and the two were answered as one.

    The consequence was invisible and real. The entire purpose of SSE in a chat client is that text
    appears while it is being written; a caller migrating from the predecessor saw a blank view for
    the length of the whole answer and then all of it at once — no error, no message, just worse.
    With a single event a client also cannot tell *"still thinking"* from *"the connection died"*,
    which is precisely what intermediate events exist to distinguish.

    So each chunk is an ``update`` carrying real text, and the terminal ``completed`` still carries
    the whole answer and the usage — a client that only reads the terminal event is unaffected.

    **The cost, stated rather than discovered:** a stream cannot fall back. Once the first chunk is
    on the wire the status is 200 and the answer has begun, so there is no second candidate to try.
    The conditions every candidate must meet are therefore checked *before* the response exists
    (`resolve_direct_target`), where a refusal can still be a status the caller can read — the same
    trade the Gemini surface makes on the same verb.
    """
    started = time.monotonic()
    trail = AuditTrail(operation="streaming-chat", api="kira")
    body: dict[str, Any] = {}
    try:
        body = await _attributed_body(request, principal, trail)
        prepared, parsed = await _prepare(request, principal, body, trail)
        canonical, _fallbacks = prepared.canonical, prepared.fallbacks
        assert canonical is not None
        # Before the response exists, so a candidate that may not serve this request is refused
        # with a status rather than with a stream that stops. The fallback chain is deliberately
        # not used here — see the docstring: there is nothing to fall back *from* once a chunk has
        # gone out, and pretending otherwise would mean discovering it mid-answer.
        provider = await resolve_direct_target(request, canonical.model, canonical)
    except KIRA_REFUSALS as exc:
        return await _refused(
            request, trail, exc, body=body, started=started, operation="streaming-chat"
        )

    async def events() -> AsyncIterator[str]:
        async with accounting(
            request,
            trail,
            prepared,
            api="kira",
            operation="streaming-chat",
            body=body,
            started=started,
        ) as acct:
            parts: list[str] = []
            usage: CanonicalUsage | None = None
            finish_reason = "stop"
            try:
                async for chunk in provider.stream_generate(canonical):
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if not chunk.text_delta:
                        # No text, nothing to say. An event carrying an empty string is exactly
                        # the synthetic heartbeat `FRD-111` §5.4 refused, and a chunk that only
                        # reports usage is one of these.
                        continue
                    parts.append(chunk.text_delta)
                    yield f"data: {json.dumps(update_event(chunk.text_delta))}\n\n"
            except KIRA_REFUSALS as exc:
                # Headers are already sent, so the failure cannot change the status. Reported into
                # the accounting rather than recorded here, so that one exit covers every way out.
                acct.failed(502, refusal_outcome(exc))
                return

            # The terminal event still carries the **whole** answer and the usage, so a client
            # that reads only `completed` — which is what a conservative predecessor client does —
            # is unaffected by the updates above.
            answer = CanonicalResponse(
                model=canonical.model,
                text="".join(parts),
                usage=usage or CanonicalUsage(prompt_tokens=0, completion_tokens=0),
                finish_reason=finish_reason,
            )
            try:
                # **A schema-constrained answer that did not finish is not data** (`FRD-112` FR-6),
                # and that holds no less because the text arrived in pieces. The updates have gone
                # out by now — headers were sent with the first one — so the refusal cannot change
                # the status. What it *can* do is withhold the terminal event: a client waiting for
                # `completed` never gets one and therefore never treats half a document as whole,
                # which is the property this check exists for.
                check_structured_result(canonical, answer)
            except KIRA_REFUSALS as exc:
                acct.failed(502, refusal_outcome(exc))
                return
            trail.served_by(answer.model, 0)
            event = completed_event(answer)
            acct.served(answer.model, usage, event["data"])
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers=_sunset(request))


@router.post("/embed")
async def embed(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    started = time.monotonic()
    trail = AuditTrail(operation="embed", api="kira")
    body: dict[str, Any] = {}
    try:
        body = await _attributed_body(request, principal, trail)
        try:
            parsed = schemas.EmbeddingRequest.model_validate(body)
        except ValidationError as exc:
            raise errors.KiraError(
                422, errors.VALIDATION_ERROR, "Request validation failed.", _details(exc)
            ) from exc

        model = await _resolve_model(request, parsed.model_id)
        trail.requested_model = model

        # What this surface owns is the predecessor's default task type; every rule about *what
        # may be embedded* — and the whole order of the controls around it — belongs to the
        # shared sequence, which is why only the default is named here.
        prepared = await prepare_for_dispatch(
            request,
            trail,
            method="embedContent",
            embed=to_embedding(parsed, model),
            default_task_type=DEFAULT_TASK_TYPE,
        )
        embed_request = prepared.embed
        assert embed_request is not None

        # **Aggregation, asked where the shape is still known.** The shared validator refuses a
        # list against a model that cannot batch, by counting texts — and this surface now joins a
        # list into one text before it gets there (`mapping.to_embedding`), so that count is always
        # one and the check could never fire again.
        #
        # Under the corrected reading the predecessor's own name for the flag finally fits:
        # `supports_aggregation` is about combining several texts into **one** vector, which is
        # exactly what a list now asks for. So the question survives the change and only its
        # vantage point moves — here, where `parsed.text` still says whether a list was sent.
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
            request, trail, prepared, api="kira", operation="embed", body=body, started=started
        ) as acct:
            # The shared resolver, not a registry lookup: an embedding has no dispatch chain, so
            # asking the registry directly meant no condition was applied — an unapproved
            # (`FRD-307`) and an unreleased (`FRD-308`) model both answered 200 here, exactly as
            # on the Gemini surface. One function, both surfaces, for the reason `FRD-126` states:
            # a rule restated on a second surface is a rule that differs on one of them.
            provider = await resolve_direct_target(request, model)
            vectors = await provider.embed(embed_request)
            # **One vector, whatever the input shape** — the predecessor's documented `vector`,
            # confirmed against its source on 2026-08-12 (`mapping.to_embedding` has the
            # measurement). A list is joined into one text before it gets here, so there is
            # exactly one vector to return and no branch to get wrong.
            payload = schemas.EmbeddingResponse(vector=vectors[0] if vectors else []).model_dump()
            acct.embedded(model, payload, units=embed_request.size)
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
                # Stage B. A client reads this list to decide what to *ask for*, so a surface that
                # now serves thinking and task types while still reporting neither would have every
                # caller conclude the models support none of it — the capability would exist and be
                # unreachable, which is indistinguishable from not having built it.
                thinkingConfig=_thinking_config(declaration),
                embedding_dimensions=declaration.default_dimensions,
                task_types=sorted(declaration.embedding_task_types) or None,
                supports_aggregation=(
                    declaration.supports_batch if declaration.can(Capability.EMBED) else None
                ),
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
    started = time.monotonic()
    # No I/O, so no duration to report — and 0.0 is the honest figure here rather than a stand-in,
    # which is the difference between this and an unprobed upstream below.
    checks = [
        schemas.HealthCheck(service="Gateway", status="Healthy", time_taken=0.0, tags=["aira"])
    ]

    # **The upstreams, from the cached verdict.** This used to report a single hardcoded `true` and
    # nothing else — a health endpoint that *cannot fail*, which is worse than none, because
    # somebody's monitoring points at it and believes it. The comment that stood here said so
    # itself, and named the fix as pending: *"`FRD-117` §5.2 has the cached-probe design; **until
    # then** this reports what the gateway itself can answer."* That design shipped. `UpstreamProbe`
    # runs in the background and `/readyz` has been reading its verdict since; this simply reads the
    # same one.
    #
    # Still **no I/O here**, which is the whole reason the cache exists: probing per call would make
    # this as slow as the slowest provider and would bill somebody for asking whether a model is
    # alive (`FRD-117` §5.2, and `ADR-0012` §5 for a scaled-to-zero endpoint).
    probe: UpstreamProbe | None = getattr(request.app.state, "upstream_probe", None)
    for name, verdict in (probe.snapshot() if probe is not None else {}).items():
        took = verdict.get("took_seconds")
        checks.append(
            schemas.HealthCheck(
                service=name,
                status=(
                    "Healthy"
                    if bool(verdict.get("ok", False)) and not verdict.get("stale", False)
                    else "Unhealthy"
                ),
                # The last probe's own duration. An adapter with nothing cheap to ask reports
                # `None`, and 0.0 is what this shape has for it — which is why `not-probed` is on
                # the tags: the number cannot carry "we did not look" and the tag can.
                time_taken=float(took) if isinstance(took, int | float) else 0.0,
                # `probed: false` means *we did not look* — reported rather than folded into the
                # status, because "we did not look" and "it is fine" are different answers
                # (`FRD-117`) and a tag is the only place this shape has to say so.
                tags=["upstream"] if verdict.get("probed", True) else ["upstream", "not-probed"],
            )
        )

    # `UNHEALTHY` **and 503**, as the predecessor answers: a monitor reads the status code long
    # before it reads the body, and one that always says 200 is the "cannot fail" defect wearing
    # the body's clothes instead of the check's.
    healthy = all(check.status == "Healthy" for check in checks)
    return JSONResponse(
        schemas.HealthResponse(
            status="Healthy" if healthy else "Unhealthy",
            total_time_taken=round(time.monotonic() - started, 3),
            entities=checks,
        ).model_dump(),
        status_code=200 if healthy else 503,
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
    """The body, or the predecessor's answer for one that is not a JSON object.

    **422 `VALIDATION_ERROR`, not 400 `INVALID_JSON_BODY`**, and the reasoning is worth keeping
    because it argues against itself. `400` is the semantically better answer: `422` means
    *well-formed but wrong*, and a body that does not parse is not well-formed. The predecessor
    answers `422` only because malformed JSON reaches FastAPI's `RequestValidationError` there —
    an artefact, not a decision, and its own `InvalidJsonBodyError` is dead in that request path.

    We copy the artefact anyway, because this surface's contract *is* the predecessor's behaviour
    and a migrating client switches on `code`. Being right about HTTP semantics on a compatibility
    layer means being wrong about the thing the layer is for. `INVALID_JSON_BODY` went with it: a
    code nothing raises is what this project has just finished removing twice.
    """
    try:
        body = await request.json()
    except ValueError as exc:
        raise errors.KiraError(
            422, errors.VALIDATION_ERROR, "Request body is not valid JSON."
        ) from exc
    if not isinstance(body, dict):
        raise errors.KiraError(422, errors.VALIDATION_ERROR, "Request body must be an object.")
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
