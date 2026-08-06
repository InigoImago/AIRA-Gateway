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

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from aira_common.logging import get_logger
from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_common.observability import set_span_attributes
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import AuditTrail, Outcome
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.core.schema import SchemaBounds, SchemaRejected
from aira_gateway.embedding import EmbeddingBounds, EmbeddingRejected
from aira_gateway.pipeline.dispatch import NoCapableModel, Permits
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.requirements import (
    MediaTypesSupported,
    RegionAllowed,
    Requirement,
    StructuredOutputSupported,
    ThinkingHonoured,
    permits,
)
from aira_gateway.residency import parse_allowed
from aira_gateway.thinking import ThinkingRejected
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError

_log = get_logger("aira_gateway")

#: Every exception a surface must treat as a refusal rather than an unhandled error. Listed once,
#: so a new control cannot be caught by one surface and escape the other.
REFUSALS = (
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


async def enforce_pre_dispatch(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    extra_tokens: int = 0,
) -> Reservation:
    """Every control a request must clear before anything expensive happens.

    Rate limiting comes first: the whole point of a limit is that the upstream call never
    happens. The budget then reserves what the request is expected to consume, so requests in
    flight are visible to each other's check instead of all passing the same stale figure.

    ``units`` is how many requests this call *is* — one, except for an embedding batch, which is
    one per text (`FRD-113` FR-6). ``extra_tokens`` is consumption no property of the body
    predicts: today a thinking budget (`FRD-111` FR-5), which can be an order of magnitude larger
    than the answer and is billed at the output rate.
    """
    attribution = getattr(request.state, "attribution", None)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)

    await request.app.state.rate_limits.check(use_case, subject, units)
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
    code, status = UPSTREAM_STATUS_MAP.get(exc.status_code or 0, (502, "UNAVAILABLE"))
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
    outcome = await engine.run(pipeline, canonical, decisions=trail.decisions)
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


_REFUSAL_OUTCOMES: dict[int, Outcome] = {
    404: Outcome.MODEL_NOT_FOUND,
    400: Outcome.INVALID_REQUEST,
    403: Outcome.BLOCKED_BY_PIPELINE,
}


def refusal_outcome(exc: Exception) -> Outcome:
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
