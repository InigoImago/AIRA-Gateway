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
from aira_gateway.core.canonical import CanonicalRequest
from aira_gateway.pipeline.dispatch import NoCapableModel, Permits
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.requirements import (
    MediaTypesSupported,
    RegionAllowed,
    Requirement,
    permits,
)
from aira_gateway.residency import parse_allowed
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError

_log = get_logger("aira_gateway")

#: Every exception a surface must treat as a refusal rather than an unhandled error. Listed once,
#: so a new control cannot be caught by one surface and escape the other.
REFUSALS = (
    AttachmentRejected,
    RateLimited,
    BudgetExceeded,
    PipelineRejected,
    NoCapableModel,
    UpstreamError,
    GeminiHTTPError,
)


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
) -> Reservation:
    """Every control a request must clear before anything expensive happens.

    Rate limiting comes first: the whole point of a limit is that the upstream call never
    happens. The budget then reserves what the request is expected to consume, so requests in
    flight are visible to each other's check instead of all passing the same stale figure.
    """
    attribution = getattr(request.state, "attribution", None)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)

    await request.app.state.rate_limits.check(use_case, subject)
    expected = await estimate(
        request, model=model, max_output_tokens=max_output_tokens, attachments=attachments
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
    price = await request.app.state.pricing.price_for(model)
    cost = 0 if price is None else cost_nanos(tokens, price.output_per_million_nanos)
    return Amounts(tokens=tokens, requests=1, cost_nanos=cost)


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
    return permits(checks)


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

    if method == "embedContent" and not declaration.can(Capability.EMBED):
        # Refused *before* dispatch rather than by an adapter raising deep in the stack: with
        # cross-vendor routing (ADR-0012) a chain can send an embedding to a model that has no
        # embedding endpoint at all, and the useful error names the model (FRD-113 FR-6a).
        raise GeminiHTTPError(
            400,
            f"Model '{model}' does not support embedding.",
            "INVALID_ARGUMENT",
        )
    if method != "embedContent" and not declaration.can(Capability.GENERATE):
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
    if isinstance(exc, AttachmentRejected):
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
