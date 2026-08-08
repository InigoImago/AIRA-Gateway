"""Everything a request goes through that is not about a particular wire format.

`FRD-107` adds a second API surface, and the rule from `ADR-0010` is that it shares **the
pre-dispatch gate, the pipeline, the dispatch chain, the audit writer and the reporting service —
everything below the surface**. Sharing it means extracting it, and this is that extraction.

Duplicating it instead would be the same mistake in a larger costume: `:embedContent` once bypassed
the pre-dispatch controls because the gate lived inside one branch rather than on the path every
branch takes. A second *surface* with its own copy of the gate is that failure with an extra
hundred lines to hide in.

What stays in a surface module: parsing its own wire format, rendering its own error envelope, and
its own routes. Everything here is about the request, not about how it was spelled.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from aira_common.logging import get_logger
from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_common.observability import set_span_attributes
from aira_gateway.anomalies.suspensions import Suspended
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome, decision_summary, tool_summary
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.core.schema import SchemaBounds, SchemaRejected
from aira_gateway.db.models import UseCaseRead
from aira_gateway.embedding import EmbeddingBounds, EmbeddingRejected
from aira_gateway.embedding import estimated_tokens as embedding_tokens
from aira_gateway.embedding import validate as validate_embedding
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import NoCapableModel, Permits
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.requirements import (
    MediaTypesSupported,
    RegionAllowed,
    Requirement,
    SamplingExpressible,
    StructuredOutputSupported,
    ThinkingHonoured,
    ToolsSupported,
    permits,
)
from aira_gateway.residency import parse_allowed
from aira_gateway.thinking import ThinkingRejected, reserved_tokens
from aira_gateway.thinking import resolve as resolve_thinking
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError

_log = get_logger("aira_gateway")

#: Every exception a surface must treat as a refusal rather than an unhandled error. Listed once,
#: so a new control cannot be caught by one surface and escape the other.
REFUSALS = (
    Suspended,
    AttachmentRejected,
    ThinkingRejected,
    SchemaRejected,
    EmbeddingRejected,
    RateLimited,
    BudgetExceeded,
    PipelineRejected,
    NoCapableModel,
    UpstreamError,
    GeminiHTTPError,
)


#: Every verb that embeds. A **set**, not a comparison against one name: `FRD-113` added the batch
#: verb, and a capability check written as ``method == "embedContent"`` would have demanded the
#: *generation* capability of it — refusing every batch against an embedding-only model, and
#: accepting one against a model that cannot embed at all. The same shape as the `:embedContent`
#: bypass, one verb later.
EMBEDDING_METHODS = frozenset({"embedContent", "batchEmbedContents"})


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


UPSTREAM_STATUS_MAP: dict[int, tuple[int, str]] = {
    429: (429, "RESOURCE_EXHAUSTED"),
    503: (503, "UNAVAILABLE"),
    504: (504, "DEADLINE_EXCEEDED"),
}

#: An upstream **400** is the provider saying *this body is invalid* — which means the request we
#: built from this deployment's catalog is one the model does not accept. That is a configuration
#: fault, and `502 UNAVAILABLE` sends whoever reads it to the provider's status page instead of to
#: the declaration that is wrong.
#:
#: Found live: the catalog declared a thinking mode the server rejects **by name**, and the caller
#: received "Upstream returned 400." with status `UNAVAILABLE`. Same reasoning as `NoCapableModel`
#: answering `FAILED_PRECONDITION` — "every candidate was excluded" and "the provider refused the
#: body we built" are both fixable by an operator; an outage is not.
#:
#: **Only 400.** A 401 or 403 is about *our* credentials, and the test this replaced was right to
#: mask those: the caller cannot act on them and the provider's message may name the credential.
#: Those stay a generic 502, and their detail stays in the log.
UPSTREAM_REFUSED = (400, "FAILED_PRECONDITION")


def upstream_status(status_code: int | None) -> tuple[int, str]:
    mapped = UPSTREAM_STATUS_MAP.get(status_code or 0)
    if mapped is not None:
        return mapped
    if status_code == 400:
        return UPSTREAM_REFUSED
    return (502, "UNAVAILABLE")


async def guard_before_work(request: Request, *, units: int = 1) -> None:
    """The controls that do not need to know the model — taken before anything is spent.

    `enforce_pre_dispatch` says in its own docstring that "rate limiting comes first: the whole
    point of a limit is that the upstream call never happens", and it was not true. The pipeline
    runs *before* it and can make a model call of its own, so a rate-limited or over-budget caller
    was refused **after** paying for a classifier. Measured against a 20 000 cost limit: one served
    request, seven refused, 72 400 spent — the refusals cost more than the answer.

    Both controls here are answerable without the model, which is why they can move and the
    reservation cannot: the reservation is made against the model routing chooses.

    Called **once, before the verb branch**, so every verb takes it. Putting it inside
    `run_pipeline` would have been tidier and would have left `:embedContent` unlimited again —
    embeddings have no pipeline. That verb is the reason this project writes controls on the path
    every branch takes rather than inside one of them (`FRD-405` B3).
    """
    attribution = getattr(request.state, "attribution", None)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)
    credential = getattr(attribution, "credential", None)

    # First of all: a caller who has been stopped is stopped, and must not pay for a classifier
    # on the way to being told (`FRD-503` FR-3). Same argument that moved rate limiting here.
    throttles = await request.app.state.suspensions.check(use_case, subject, credential)
    await request.app.state.rate_limits.check(use_case, subject, units, extra=throttles)
    await request.app.state.budgets.refuse_if_exhausted(use_case, subject)
    request.state.early_gate_taken = True


async def enforce_pre_dispatch(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    extra_tokens: int = 0,
) -> Reservation:
    """The reservation — the control that has to know which model will serve the request.

    Rate limiting and the "already over budget" check moved to :func:`guard_before_work`, which
    runs before the pipeline; they need no model, and leaving them here meant a refused request had
    already paid for a classifier call. What stays is the reservation, which cannot move: it is
    made against the model **routing** chooses, so it has to happen after the pipeline.

    The reservation is what makes requests in flight visible to each other's check instead of all
    passing the same stale figure.

    ``units`` is how many requests this call *is* — one, except for an embedding batch, which is
    one per text (`FRD-113` FR-6). ``extra_tokens`` is consumption no property of the body
    predicts: today a thinking budget (`FRD-111` FR-5), which can be an order of magnitude larger
    than the answer and is billed at the output rate.
    """
    if not getattr(request.state, "early_gate_taken", False):
        # A surface that reserves without having taken `guard_before_work` is a surface with no
        # rate limiting, and the first draft of this change was exactly that: the take moved out of
        # here and into the Gemini routes, leaving the KIRA surface unlimited on all three verbs.
        # Nothing failed, because no test asked whether *that* surface was limited.
        #
        # So the two are wired together rather than merely both present. Reaching a reservation
        # without the gate is a programming error and says so, instead of quietly serving traffic
        # nobody is metering.
        raise RuntimeError(
            "enforce_pre_dispatch reached without guard_before_work; this surface would serve "
            "unmetered traffic. Take the early gate before the pipeline."
        )

    attribution = getattr(request.state, "attribution", None)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)

    expected = await estimate(
        request,
        model=model,
        max_output_tokens=max_output_tokens,
        attachments=attachments,
        units=units,
        extra_tokens=extra_tokens,
    )
    # `app.state` is untyped, so the annotation is what states the contract the route relies on.
    reservation: Reservation = await request.app.state.budgets.guard(
        use_case, subject, estimated=expected
    )
    return reservation


async def estimate(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    extra_tokens: int = 0,
) -> Amounts:
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
    declaration = await catalog_of(request).declaration(model)
    tokens = declaration.output_cap(max_output_tokens) or settings.budget_estimate_output_tokens
    # Attachments are input, and input a character count cannot predict (FRD-110 §5.3). Priced at
    # the output rate along with everything else: over-reserving briefly is the safe direction for
    # a spend limit, and the figure is corrected the moment the response arrives.
    tokens += declaration.attachment_tokens(attachments or [])
    # A thinking budget is the largest single number on a request that uses it, and it is billed
    # as output. Reserving without it would leave the most expensive knob on the request invisible
    # to the limit that exists to bound spend.
    tokens += extra_tokens
    price = await request.app.state.pricing.price_for(model)
    cost = 0 if price is None else cost_nanos(tokens, price.output_per_million_nanos)
    return Amounts(tokens=tokens, requests=units, cost_nanos=cost)


def upstream_error(exc: UpstreamError) -> JSONResponse:
    """Map an upstream failure onto a client-facing status.

    Not a surface concern despite returning a response: which upstream statuses are worth passing
    through is a fact about the *upstream*, and both surfaces have to agree on it or the same
    outage would look like two different problems depending on which URL was called.
    """
    code, status = upstream_status(exc.status_code)
    return _error(code, exc.message, status)


def registry_of(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


def requirements_for(request: Request, canonical: CanonicalRequest | None) -> Permits:
    """What a candidate must satisfy to serve this request (`ADR-0012` §3).

    Assembled per request because the answer depends on the request: residency always, and the
    attachment media types only when the caller actually sent one.
    """
    settings = request.app.state.settings
    # One list, every transport (`ADR-0012` §6) — reading a Vertex-named setting here would make
    # the first Azure model fail a check named after Google.
    checks: list[Requirement] = [
        RegionAllowed(registry_of(request), parse_allowed(settings.allowed_regions))
    ]
    if canonical is not None and canonical.media_types:
        checks.append(MediaTypesSupported(catalog_of(request), canonical.media_types))
    if canonical is not None and canonical.response_schema is not None:
        checks.append(StructuredOutputSupported(catalog_of(request)))
    if canonical is not None and canonical.thinking is not None:
        checks.append(ThinkingHonoured(catalog_of(request), canonical.thinking))
    if canonical is not None and canonical.sampling_requested:
        checks.append(SamplingExpressible(registry_of(request), canonical.sampling_requested))
    if canonical is not None and canonical.tools:
        checks.append(ToolsSupported(catalog_of(request)))
    return permits(checks)


def schema_bounds(request: Request) -> SchemaBounds:
    settings = request.app.state.settings
    return SchemaBounds(
        max_bytes=settings.max_response_schema_bytes,
        max_depth=settings.max_response_schema_depth,
        max_properties=settings.max_response_schema_properties,
    )


def embedding_bounds(request: Request) -> EmbeddingBounds:
    settings = request.app.state.settings
    return EmbeddingBounds(
        max_batch=settings.max_embedding_batch,
        max_total_chars=settings.max_embedding_chars,
    )


def check_structured_result(canonical: CanonicalRequest, response: CanonicalResponse) -> None:
    """A schema-constrained answer that did not finish normally is not data (`FRD-112` FR-6).

    Providers differ in how faithfully they honour a schema, and the two ways it goes wrong look
    identical from the outside: a document truncated at the output cap is still valid-looking JSON
    right up to where it stops, and an Anthropic model that answered in prose instead of calling
    the forced tool produced no document at all. Returning either as though it were the requested
    shape hands a parse error — or worse, a *successful* parse of half the data — to somebody
    else's application.
    """
    if canonical.response_schema is None or response.finish_reason == "stop":
        return
    raise GeminiHTTPError(
        502,
        f"The model did not return a complete document matching the requested schema "
        f"(it stopped with '{response.finish_reason}').",
        "FAILED_PRECONDITION",
    )


def provenance(request: Request, model: str) -> tuple[str, str, str] | None:
    """Where the request was processed, from the adapter that serves the model.

    Read from the registry rather than the catalog: the catalog says where a model is *configured*
    to run, the registry says which adapter actually holds it. Under a residency requirement the
    second is the one worth recording.
    """
    described = registry_of(request).get_model(model)
    if described is None or not described.provider:
        return None
    return (described.provider, described.publisher, described.region)


def catalog_of(request: Request) -> ModelCatalog:
    catalog: ModelCatalog = request.app.state.catalog
    return catalog


def check_not_empty(canonical: CanonicalRequest) -> None:
    """Refuse a request that asks nothing (`FRD-113` FR-7's rule, applied to generation).

    Both surfaces call it, because a no-op that costs money is a no-op on either of them.
    """
    if canonical.is_empty:
        raise GeminiHTTPError(
            400,
            "The request carries no text and no attachment. It would be billed for an answer to "
            "nothing.",
            "INVALID_ARGUMENT",
        )


async def check_tools_permitted(request: Request, canonical: CanonicalRequest) -> None:
    """A use case may declare functions only if somebody turned that on (`FRD-131` FR-3).

    **Free for every request that declares nothing**, which is nearly all of them: the read only
    happens when `tools` is non-empty, so a chatbot pays no price for a capability it never uses.

    Refused with `FAILED_PRECONDITION` rather than `PERMISSION_DENIED`: the caller's credential is
    fine and the request is well formed — what is missing is a *configuration* somebody can change,
    and the message says who. `ADR-0012`'s vocabulary, for the same reason `NoCapableModel` uses
    it: operator-fixable is not the same answer as "you may not".
    """
    if not canonical.tools:
        return
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    if use_case is None:
        raise GeminiHTTPError(
            400,
            "Tool calling is configured per use case, and this request names none. Send it with a "
            "use case that has tool calling enabled.",
            "FAILED_PRECONDITION",
        )
    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        record = await session.get(UseCaseRead, use_case)
    if record is None or not record.tools_enabled:
        raise GeminiHTTPError(
            400,
            f"Use case '{use_case}' has not enabled tool calling. An administrator of the use "
            "case can turn it on; it is off by default so that only the use cases which need "
            "functions can declare them.",
            "FAILED_PRECONDITION",
        )


async def check_declaration(
    request: Request, *, model: str, method: str, requested: int | None
) -> ModelDeclaration:
    """Every rule the catalog decides, before anything expensive happens (FRD-114).

    Returns the declaration, so the caller can act on ``deprecated`` without a second lookup.
    """
    declaration = await catalog_of(request).declaration(model)

    # Whether a model may *embed* is decided by `embedding.validate`, which both surfaces call —
    # not here as well. The check lived in both for a while and the mutation harness caught it:
    # removing either copy changed nothing observable, which is what redundancy looks like from
    # the outside and is a defect in the making. Two places deciding one rule drift, and the one
    # that drifts is whichever is not under test.
    if method not in EMBEDDING_METHODS and not declaration.can(Capability.GENERATE):
        raise GeminiHTTPError(
            400, f"Model '{model}' does not support generation.", "INVALID_ARGUMENT"
        )

    if requested is not None and requested <= 0:
        # Found live. A negative cap was accepted, and `words[:limit]` with a negative limit does
        # not mean "no limit" — it silently drops the end of the answer. A real vendor rejects it
        # with a message about a field the caller cannot map back to their own request, so it is
        # refused here where the name still matches what they sent.
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens must be a positive number of tokens, not {requested}.",
            "INVALID_ARGUMENT",
        )

    cap = declaration.max_output_tokens
    if requested is not None and cap is not None and requested > cap:
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens {requested} exceeds the {cap} this model accepts.",
            "INVALID_ARGUMENT",
        )
    return declaration


def deprecation_headers(declaration: ModelDeclaration) -> dict[str, str]:
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


async def run_pipeline(
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
    try:
        outcome = await engine.run(
            pipeline, canonical, decisions=trail.decisions, model_calls=trail.model_calls
        )
    finally:
        # **One site**, and it is in the `finally` on purpose: a filter that blocked still spent
        # the tokens it took to decide that, and a use case running a blocking filter over rejected
        # traffic is paying for exactly those. Both surfaces reach it because both call this
        # function — the alternative was a hook at each surface's boundary, which is the shape that
        # let `:embedContent` slip past the pre-dispatch gate.
        await record_pipeline_calls(request, trail)
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


@dataclass(slots=True)
class Prepared:
    """Everything the pre-dispatch sequence decided, handed over in one piece."""

    canonical: CanonicalRequest | None
    embed: CanonicalEmbeddingRequest | None
    fallbacks: tuple[str, ...]
    declaration: ModelDeclaration
    reservation: Reservation

    @property
    def model(self) -> str:
        """The model that will actually serve this — after routing, not as the caller spelled it."""
        if self.canonical is not None:
            return self.canonical.model
        assert self.embed is not None
        return str(self.embed.model)


async def prepare_for_dispatch(
    request: Request,
    trail: AuditTrail,
    *,
    method: str,
    canonical: CanonicalRequest | None = None,
    embed: CanonicalEmbeddingRequest | None = None,
    requested_output: int | None = None,
    default_task_type: str | None = None,
) -> Prepared:
    """The whole pre-dispatch sequence, in the one place that owns its order.

    This function exists because sharing the *steps* turned out not to be the same as sharing the
    *sequence*. `serving.py` already held every one of these, and both surfaces still wrote the
    order out by hand — the same six calls, twice, and in the KIRA surface spread across four
    functions. A third surface (`FRD-106`) would have written them a third time.

    That is not tidiness. **Every property this layer guarantees is a property of the order:**

        rate limit before the pipeline   or a refused request pays for a classifier call
        declaration after routing        or a cap is checked against a model that never serves it
        thinking after routing           or a budget is validated against the wrong model
        reservation last                 or it is made against the model the caller *named*

    None of those is expressible in a function that only knows its own step, which is why the gap
    kept reappearing: `:embedContent` bypassing the gate, then the KIRA surface losing its rate
    limiting entirely. A surface that assembles the order can assemble it wrong; a surface that
    calls this cannot.

    What is left to a surface is what its own docstring always claimed: parse its wire format,
    render its error envelope, own its routes.
    """
    if canonical is not None:
        check_not_empty(canonical)
        # Before the bucket and the pipeline. A request that can never succeed should not spend
        # the caller's rate-limit allowance on the way to being refused, and it must not pay for
        # a classifier call (`FRD-125b`) either.
        await check_tools_permitted(request, canonical)

    units = embed.size if embed is not None else 1

    # Weighed first — a batch weighs what it is (`FRD-113` FR-6) — and then the controls that need
    # no model, before the pipeline, which can make a model call of its own.
    await guard_before_work(request, units=units)

    fallbacks: tuple[str, ...] = ()
    if canonical is not None:
        canonical, fallbacks = await run_pipeline(request, canonical, trail)
        if registry_of(request).provider_for(canonical.model) is None:
            # Routing sent it somewhere nobody serves. Raised as the shared error so each surface
            # renders it in its own envelope rather than each checking for itself.
            raise GeminiHTTPError(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")

    served = canonical.model if canonical is not None else (embed.model if embed else "")
    declaration = await check_declaration(
        request, model=served, method=method, requested=requested_output
    )

    if canonical is not None:
        canonical = canonical.model_copy(
            update={"thinking": resolve_thinking(canonical.thinking, declaration)}
        )
    if embed is not None:
        embed = validate_embedding(
            embed, declaration, embedding_bounds(request), default_task_type=default_task_type
        )

    reservation = await enforce_pre_dispatch(
        request,
        model=served,
        max_output_tokens=requested_output,
        attachments=[part.media_type for part in canonical.attachments] if canonical else None,
        units=units,
        extra_tokens=(
            reserved_tokens(canonical.thinking)
            if canonical is not None
            else embedding_tokens(embed)
            if embed is not None
            else 0
        ),
    )
    return Prepared(canonical, embed, fallbacks, declaration, reservation)


@dataclass(slots=True)
class Accounting:
    """What a request produced, reported into the sequence that will account for it.

    A caller sets `response` (or `vectors`, or neither) and the exit does the rest. Reporting into
    an object rather than calling settle/record inline is what lets **one** exit cover every way
    out — served, refused, or cancelled while the model was still answering.
    """

    response: CanonicalResponse | None = None
    payload: dict[str, Any] | None = None
    usage: CanonicalUsage | None = None
    model: str = ""
    #: `499` — the caller left. Nobody is sent it; it exists so the audit can tell that case from
    #: a served one. Overwritten by whatever actually happened.
    status: int = 499
    outcome: Outcome | None = None
    #: Whether anything was produced at all. Distinct from `usage is None`, because an embedding
    #: produces vectors and reports **no tokens** — settling it as "nothing" would hand back a
    #: batch's whole reservation and leave batched traffic invisible to a request limit.
    produced: bool = False
    #: What this call weighed against a request-limited budget: one, or one per text in a batch.
    requests: int = 1

    #: Set by `accounting`, so `served` can record a tool call without either exit having to
    #: remember to. Both exits calling the same method is the point: `FRD-126`'s lesson is that a
    #: fact recorded at one `return` is a fact eventually missing from another, and this one *was*
    #: missing from the streamed exit — a real assistant turn stored `{"text": ""}` and no more.
    trail: AuditTrail | None = None

    def served(
        self,
        model: str,
        usage: CanonicalUsage | None,
        payload: dict[str, Any],
        tool_calls: Sequence[str] = (),
    ) -> None:
        self.model = model
        self.usage = usage
        self.payload = payload
        self.status = 200
        self.outcome = Outcome.SERVED
        self.produced = True
        if tool_calls and self.trail is not None:
            self.trail.tool_calls = list(tool_calls)

    def embedded(self, model: str, payload: dict[str, Any], *, units: int) -> None:
        """Vectors, which cost tokens nobody reports. Weighed as the many requests it is."""
        self.served(model, None, payload)
        self.requests = units

    def failed(self, status: int, outcome: Outcome) -> None:
        self.status = status
        self.outcome = outcome


@asynccontextmanager
async def accounting(
    request: Request,
    trail: AuditTrail,
    prepared: Prepared,
    *,
    api: str,
    operation: str,
    body: dict[str, Any] | None,
    started: float,
) -> AsyncIterator[Accounting]:
    """Hold the reservation, and account for the request **however it ends**.

    The companion to `prepare_for_dispatch`, and it exists for the same reason: the post-dispatch
    steps — hold, dispatch, price, settle, record — were written out once per verb per surface,
    six times, and their *order* is the guarantee. `FRD-126` consolidated the half before dispatch;
    this is the half after.

    Asked whether every path had been tested with a dropped connection, the answer was no, and the
    check found that **four of the six lost the audit row**: a caller who went away while the model
    was answering made a request that reached the upstream disappear from the record. `FRD-122`'s
    rule does not care how a request ended — the log records what was asked.

    **Shielded.** Closing a generator in-process raises `GeneratorExit` and awaits in a `finally`
    run normally; a caller dropping a real socket cancels the task, and a bare `await` here
    re-raises `CancelledError` at its first suspension point, losing exactly the work this function
    exists to do. That was found as a 1-in-8 integration flake (`FRD-110`) on the one path that
    had it; now no path can be without it.

    Nothing chargeable produced means the reservation is **released**, not settled: booking a
    request against somebody who received nothing would spend a request limit on a caller who hung
    up, or on an upstream outage.
    """
    state = Accounting(model=prepared.model, trail=trail)
    # The declaration is a property of the request and is known here, before dispatch — recorded
    # once for every surface and every verb, rather than at whichever exit remembers.
    trail.tools_declared = len(prepared.canonical.tools) if prepared.canonical is not None else 0
    record = True
    # The accounting runs **inside** `hold`, not around it. Outside, `hold` sees an unresolved
    # reservation on the way out and gives it back — and then the settle books it again. One
    # request, settled once and released once, which a test caught immediately.
    async with request.app.state.budgets.hold(prepared.reservation):
        try:
            yield state
        except asyncio.CancelledError, GeneratorExit:
            # The caller left. Nobody will render a response and nobody else will write a row,
            # so this is the only place the request can be recorded — which is exactly what four
            # of the six paths were failing to do.
            raise
        except BaseException:
            # A refusal on its way to the surface's exception boundary, which knows the status and
            # the outcome vocabulary for it and writes the row itself. Writing a *second* row here
            # would double-count every failed request.
            record = False
            raise
        finally:
            await asyncio.shield(
                _settle_and_record(
                    request,
                    trail,
                    prepared,
                    state,
                    api=api,
                    operation=operation,
                    body=body,
                    started=started,
                    record=record,
                )
            )


async def _settle_and_record(
    request: Request,
    trail: AuditTrail,
    prepared: Prepared,
    state: Accounting,
    *,
    api: str,
    operation: str,
    body: dict[str, Any] | None,
    started: float,
    record: bool = True,
) -> None:
    model = state.model or prepared.model
    cost = await request.app.state.pricing.cost_nanos(model, state.usage)
    if not state.produced:
        # Nothing chargeable, so nothing is settled — and nothing is released here either, because
        # `hold` has already given an unresolved reservation back by the time this runs. Releasing
        # again would count the give-back twice, which a test caught within the minute.
        #
        # The rule itself stands: settling would still book one request, so a use case with a
        # request limit would lose allowance to a caller who hung up or an upstream that failed.
        cost = None
    else:
        await request.app.state.budgets.settle(
            prepared.reservation,
            state.usage.total_tokens if state.usage else 0,
            cost_nanos=cost,
            requests=state.requests,
        )
    if not record:
        return
    await record_request(
        request,
        operation=operation,
        model=trail.served_model or model,
        status=state.status,
        usage=state.usage,
        latency_ms=elapsed_ms(started),
        request_payload=body,
        response_payload=state.payload,
        cost_nanos=cost,
        outcome=state.outcome or Outcome.CLIENT_GONE,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        # Both exits pass through here (`FRD-126`), which is the only reason the streamed path
        # gets this for free — it did not, for an afternoon, and the row read `{"text": ""}`.
        tool_calls=tool_summary(trail),
        provenance=provenance(request, model),
        api=api,
    )


async def record_pipeline_calls(request: Request, trail: AuditTrail) -> None:
    """Audit and bill the model calls the **pipeline** made (`FRD-125`).

    One caller request with an LLM step makes two model calls and used to leave one audit row. The
    second was invisible three ways: reporting showed a spend it was not part of, the budget
    counters never saw it, and `ADR-0013`'s auditable model access had a model call in it that
    nothing recorded.

    Called from the surface's boundary for **served and refused requests alike**, and that is the
    part worth stating: a filter that blocked still spent the tokens it took to decide that, and a
    use case running a blocking filter over rejected traffic is paying for precisely those.

    Never allowed to fail the request. The caller's own row is already written by the time this
    runs, and losing the classifier's row is worse than turning a correct answer into a 500 —
    said loudly instead.
    """
    if not trail.model_calls:
        return
    try:
        attribution = getattr(request.state, "attribution", None)
        for call in trail.model_calls:
            cost = await request.app.state.pricing.cost_nanos(call.model, call.usage)
            await record_request(
                request,
                # Named for the step, so the reporting breakdown separates "what the use case
                # asked" from "what governing it cost" instead of blending them into one figure.
                operation=f"pipeline:{call.step}",
                model=call.model,
                status=200,
                usage=call.usage,
                latency_ms=None,
                # Never the prompt. The classifier is *sent* the caller's text, and storing it a
                # second time under a different row would double every retention and redaction
                # question this system has (`FRD-404`, `FRD-406`).
                request_payload=None,
                response_payload=None,
                cost_nanos=cost,
                outcome=Outcome.SERVED,
                requested_model=call.model,
                provenance=provenance(request, call.model),
            )
            await request.app.state.budgets.book_side_call(
                getattr(attribution, "use_case", None),
                getattr(attribution, "subject", None),
                call.usage.total_tokens,
                cost_nanos=cost,
            )
    except Exception:  # noqa: BLE001 — see above
        _log.error("pipeline_call_not_recorded", operation=trail.operation, exc_info=True)


_REFUSAL_OUTCOMES: dict[int, Outcome] = {
    404: Outcome.MODEL_NOT_FOUND,
    400: Outcome.INVALID_REQUEST,
    403: Outcome.BLOCKED_BY_PIPELINE,
}


def refusal_outcome(exc: Exception) -> Outcome:
    # Checked before `RateLimited`, and its own value: "we stopped this caller on purpose" and
    # "this caller is going too fast" want different answers from whoever reads the report.
    if isinstance(exc, Suspended):
        return Outcome.SUSPENDED
    if isinstance(exc, AttachmentRejected | ThinkingRejected | SchemaRejected | EmbeddingRejected):
        return Outcome.INVALID_REQUEST
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
