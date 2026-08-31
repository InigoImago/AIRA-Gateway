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
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from aira_common.logging import get_logger
from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_common.observability import set_span_attributes
from aira_gateway.anomalies.suspensions import Suspended, SuspensionService
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.errors import gemini_error_response as _error
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.audit import (
    PIPELINE_OPERATION_PREFIX,
    AuditTrail,
    Outcome,
    decision_summary,
    redaction_failed,
    tool_summary,
)
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import BudgetService, Reservation
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
from aira_gateway.pipeline.dispatch import (
    NoCapableModel,
    Permits,
    Routing,
    RoutingOf,
    Skipped,
)
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.ratelimit.buckets import per_minute
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.ratelimit.service import RateLimitService
from aira_gateway.requirements import (
    MediaTypesSupported,
    ModelApproved,
    ModelReleasedForUseCase,
    RegionAllowed,
    Requirement,
    SamplingExpressible,
    SchemaExpressible,
    StructuredOutputSupported,
    ThinkingHonoured,
    ToolsSupported,
    permits,
)
from aira_gateway.residency import parse_allowed
from aira_gateway.state import (
    budgets_of,
    pricing_of,
    rate_limits_of,
    sessionmaker_of,
    settings_of,
    suspensions_of,
)
from aira_gateway.thinking import ThinkingRejected, reserved_tokens
from aira_gateway.thinking import resolve as resolve_thinking
from aira_gateway.upstreams.base import (
    DialectUnsupported,
    ProviderRegistry,
    Upstream,
    UpstreamError,
)

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
    # **A mapping that cannot express the request is a refusal, not a crash.** Its own docstring
    # called it "unreachable in practice — a model that cannot do a thing does not declare the
    # capability" — and the console is where an administrator declares one. Ticking `auto` for a
    # model on an OpenAI-dialect server took ten seconds and turned every thinking request into a
    # **500 "Internal error"**: the caller learned nothing, the operator learned nothing, and the
    # audit row said the gateway had broken rather than that a declaration was wrong.
    DialectUnsupported,
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
    # **The person, not the credential.** A key and a sign-in by the same human share one
    # allowance (`aira_gateway.scopes.person`); the subject alone gave them two, because the two
    # credentials answer "who is this" in different alphabets.
    #
    # A **suspension** still reads the subject and the credential: stopping traffic is aimed at
    # exactly one of the three — a person, a credential, or a use case — and folding the first two
    # together would make "block this leaked key" stop the person holding it.
    subject = getattr(attribution, "subject", None)
    caller = getattr(attribution, "person", None)
    credential = getattr(attribution, "credential", None)

    # First of all: a caller who has been stopped is stopped, and must not pay for a classifier
    # on the way to being told (`FRD-503` FR-3). Same argument that moved rate limiting here.
    #
    # **Annotated, and that is the point.** `app.state` is untyped, so every call made through it
    # is unchecked — and this seam was broken: `check` returns `Throttle`, `RateLimitService.check`
    # takes `BucketRequest`, and the two share no field the limiter reads. A `throttle` suspension
    # therefore raised `AttributeError` and became a **500** for every request from the caller it
    # was meant to slow down, while the console showed the decision as active. The same shape as
    # `FRD-125`'s badge-wearing absent control, and invisible to mypy for exactly this reason. The
    # annotations are what let the type checker see the contract; `per_minute` is what makes the
    # two vocabularies meet in one place instead of at a call site.
    suspensions: SuspensionService = suspensions_of(request)
    rate_limits: RateLimitService = rate_limits_of(request)
    budgets: BudgetService = budgets_of(request)
    # `caller` as well as `subject`: a suspension naming a **person** has to stop them whichever
    # credential they hold, and the two disagree about the subject (`FRD-613`). The credential
    # target stays the credential, which is what makes "block this leaked key" a different act
    # from "stop this person".
    throttles = await suspensions.check(use_case, subject, credential, caller)
    await rate_limits.check(
        use_case,
        caller,
        units,
        extra=[per_minute(t.key, t.limit_rpm, label=t.label) for t in throttles],
    )
    await budgets.refuse_if_exhausted(use_case, caller)
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
    # The reservation is against the same pot the gate above checked, or a caller could be waved
    # through one and refused by the other for the same traffic.
    caller = getattr(attribution, "person", None)

    expected = await estimate(
        request,
        model=model,
        max_output_tokens=max_output_tokens,
        attachments=attachments,
        units=units,
        extra_tokens=extra_tokens,
    )
    # `app.state` is untyped, so the annotation is what states the contract the route relies on.
    reservation: Reservation = await budgets_of(request).guard(use_case, caller, estimated=expected)
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
    settings = settings_of(request)
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
    price = await pricing_of(request).price_for(model)
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


async def requirements_for(request: Request, canonical: CanonicalRequest | None) -> Permits:
    """What a candidate must satisfy to serve this request (`ADR-0012` §3).

    Assembled per request because the answer depends on the request: residency always, and the
    attachment media types only when the caller actually sent one.

    **Async since `FRD-308`**, because one of the checks is a fact about the caller's use case
    rather than about the request — read **once here** and then asked per hop, so a chain of five
    candidates is one query rather than five.
    """
    settings = settings_of(request)
    # One list, every transport (`ADR-0012` §6) — reading a Vertex-named setting here would make
    # the first Azure model fail a check named after Google.
    checks: list[Requirement] = [
        RegionAllowed(registry_of(request), parse_allowed(settings.allowed_regions)),
        # Unconditional, unlike the rest: every other check here depends on what the *request*
        # asked for, and this one is a property of the installation. Whether a model may be used
        # at all is not a question a request gets to make go away (`FRD-307`).
        ModelApproved(catalog_of(request), registry_of(request)),
        # The second gate, with a different owner (`FRD-308`). `ModelApproved` is the
        # installation's decision about what may be used at all; this is the use case's own
        # administrator's about which of those it reaches. Unconditional for the same reason: not
        # a question a request gets to make go away.
        ModelReleasedForUseCase(
            await released_models(request), use_case_of(request), registry_of(request)
        ),
    ]
    if canonical is not None and canonical.media_types:
        checks.append(MediaTypesSupported(catalog_of(request), canonical.media_types))
    if canonical is not None and canonical.response_schema is not None:
        checks.append(StructuredOutputSupported(catalog_of(request)))
        # Whether the *model* offers structured output, and whether the *dialect* can carry this
        # particular schema, are two questions — `ADR-0011` rule 3 in its usual shape.
        checks.append(
            SchemaExpressible(registry_of(request), canonical.response_schema, catalog_of(request))
        )
    if canonical is not None and canonical.thinking is not None:
        checks.append(ThinkingHonoured(catalog_of(request), canonical.thinking))
    if canonical is not None and canonical.sampling_requested:
        checks.append(
            SamplingExpressible(
                registry_of(request), canonical.sampling_requested, catalog_of(request)
            )
        )
    if canonical is not None and canonical.tools:
        checks.append(ToolsSupported(catalog_of(request)))
    return permits(checks)


def ensure_body_is_encodable(body: Any) -> None:
    """Refuse a body carrying text that cannot be written as UTF-8, before anything is spent.

    JSON may escape any code unit, including **half of a surrogate pair**: `"\\ud800"` parses
    happily into a Python string that no UTF-8 encoder will accept. Nothing on the request path
    notices until the upstream call is *built* — inside httpx, nine steps later — and by then the
    rate limit is spent, the budget is reserved, the pipeline has run and possibly paid for a
    classifier, and the caller gets a **500** for a body they sent.

    Measured on 2026-08-19 against both surfaces: `500 INTERNAL_SERVER_ERROR`, and **no audit row
    at all** — the recording sites cover a request that reached an upstream, and this one dies one
    step before. So a caller could consume controls and leave no trace by sending six characters.

    A caller error must not be a server error (`FRD-124`'s rule from the other side), so this is a
    `400` naming what is wrong. Checked with one pass in C rather than by walking the structure:
    `ensure_ascii=False` is what makes `dumps` emit the character instead of re-escaping it, which
    is what makes the encoder object.
    """
    try:
        # `allow_nan=False` rejects `Infinity`, `-Infinity` and `NaN`, which Python's parser
        # accepts and **RFC 8259 does not have**. They were the other half of this defect and the
        # worse half: `maxTokens: 1e309` was correctly refused with a 422 and then the *audit row*
        # failed to write, because Postgres will not take `Infinity` in a `json` column. Six
        # characters and a request left no trace at all. A caller must not be able to choose
        # whether they are recorded.
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a character that is not valid UTF-8 — an unpaired "
            f"surrogate ({exc.object[exc.start : exc.end]!r}). JSON can escape one, and nothing "
            "can send it.",
            "INVALID_ARGUMENT",
        ) from exc
    except ValueError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a number JSON cannot represent — `Infinity`, `-Infinity` "
            "or `NaN`. Python's parser accepts them and the standard does not, so nothing "
            "downstream can store or forward one.",
            "INVALID_ARGUMENT",
        ) from exc
    except TypeError as exc:  # pragma: no cover - a body that parsed will serialise
        raise GeminiHTTPError(
            400, "The request body is not representable as JSON.", "INVALID_ARGUMENT"
        ) from exc


def schema_bounds(request: Request) -> SchemaBounds:
    settings = settings_of(request)
    return SchemaBounds(
        max_bytes=settings.max_response_schema_bytes,
        max_depth=settings.max_response_schema_depth,
        max_properties=settings.max_response_schema_properties,
    )


def embedding_bounds(request: Request) -> EmbeddingBounds:
    settings = settings_of(request)
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


async def provenance(
    request: Request, model: str, served_region: str = ""
) -> tuple[str, str, str] | None:
    """Where the request was processed, from the adapter that serves the model.

    **`served_region` wins whenever the adapter reported one** (`FRD-609`). Everything below
    answers from *configuration* — which region a model is set up in — and that was the same
    sentence as "where this request went" only for as long as a model had one region. With a
    failover chain it is not: a request served from the second region would otherwise be recorded
    at the first, and a residency claim naming a place the request did not go to is worse than a
    blank one, because it reads as evidence.

    The registry first: the catalog says where a model is *configured* to run, the registry says
    which adapter actually holds it, and under a residency requirement the second is the one worth
    recording.

    A model resolved through the **catalog** rather than through configuration (`FRD-507`) has no
    registry entry, so the adapter that owns its provider answers instead. Without that step the
    row went out with an empty provider and region — worse than the second list the feature
    removes, because `FRD-115`'s whole point is that a blank column is neither a claim nor
    evidence.
    """
    registry = registry_of(request)
    described = registry.get_model(model)
    if described is not None and described.provider:
        return (described.provider, described.publisher, served_region or described.region)

    declared = await catalog_of(request).declaration(model)
    if not declared.provider:
        return None
    configured = registry.provenance_for(declared.provider)
    if configured is None:
        return None
    return (configured[0], configured[1], served_region or configured[2])


def catalog_of(request: Request) -> ModelCatalog:
    """The catalog, as **this request's** view of it — see `ModelCatalog.per_request`.

    Held on `request.state` so every caller in the request shares one, which is the whole point:
    five readers asking about the same model used to be five sessions and five round trips.
    """
    memoised: ModelCatalog | None = getattr(request.state, "catalog", None)
    if memoised is None:
        source: ModelCatalog = request.app.state.catalog
        memoised = source.per_request()
        request.state.catalog = memoised
    return memoised


async def use_case_record(request: Request, slug: str | None) -> UseCaseRead | None:
    """This request's use case from the read-model, read **once**.

    Four callers wanted the same row and each opened its own session for it — the release
    (`FRD-308`), the tool-calling switch (`FRD-131`), and both halves of prompt caching
    (`FRD-133`). Same argument as `catalog_of`, same lifetime, and the same reason it is a
    request's and not the app's: this row carries a use case's release and its storage switch, and
    a stale copy of either is a control that keeps applying after somebody changed it.

    ``None`` is cached too. "Not in the read-model" is an answer the callers read differently from
    each other — `released_for` reads it as *nobody has told us* — and re-asking would make the
    same request able to get two of them.
    """
    if not slug:
        return None
    seen: dict[str, UseCaseRead | None] = getattr(request.state, "use_cases", None) or {}
    if slug not in seen:
        async with sessionmaker_of(request)() as session:
            seen[slug] = await session.get(UseCaseRead, slug)
        request.state.use_cases = seen
    return seen[slug]


async def refuse_if_retired(request: Request) -> None:
    """Refuse a request to a use case that has been retired (`FRD-607`).

    **This check is only possible because the row survives.** `_delete_usecase` used to remove it,
    and its own comment explains why an existence check at authentication would be wrong: keys and
    use cases arrive on different Kafka topics with no ordering between them, so a use case that
    has not arrived yet looks exactly like one that was deleted. Refusing on *absence* would refuse
    a use case created a second ago.

    A tombstone is not absence. It is positive knowledge that Management retired this slug, and it
    can only exist after the use case was known — so the ordering argument does not apply and the
    refusal is safe.

    It closes a hole retirement would otherwise have left open. API keys stop working because the
    same event deactivates them, and group *grants* go because the read-model row goes. But the
    `/use-cases/<slug>` Keycloak group resolves **from the token alone** (`auth/oidc.py`), touching
    no AIRA table — so every OIDC member of a retired use case could have gone on calling it, with
    the use case's own controls deleted underneath them: no budget, no rate limit, no pipeline.
    Retiring a compromised use case has to stop the traffic, or it is a filing action.
    """
    attribution = getattr(request.state, "attribution", None)
    slug = getattr(attribution, "use_case", None)
    record = await use_case_record(request, slug)
    if record is not None and record.deleted_at is not None:
        # `403`, not `404`. The caller's credential is valid and their membership is real; what
        # changed is that the use case was retired, and saying so is the answer that lets somebody
        # stop retrying and go and ask. A `404` would read as "wrong slug".
        raise GeminiHTTPError(
            403,
            f"Use case '{slug}' has been retired and no longer serves requests. Its record is "
            "kept for audit; ask a Global Administrator if you believe this is wrong.",
            "PERMISSION_DENIED",
        )


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


async def resolve_reasoning(
    request: Request, canonical: CanonicalRequest, *, asked_for: bool
) -> CanonicalRequest:
    """Whether this request may have the model's reasoning back (`FRD-135` FR-3/FR-4).

    Decided by the **use case**, never by the caller. Two outcomes and no third:

    - the use case has reasoning on → the canonical request carries it, the adapter asks the
      provider for thoughts, and they come back marked;
    - it does not, and the caller asked → **refused by name**. Answering 200 with no thoughts is
      the silent drop `FRD-124` exists against, and it is what the schema-level refusal did before
      this became a per-use-case decision.

    A caller who asked for nothing is unaffected either way, which is nearly all of them.
    """
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    record = await use_case_record(request, use_case) if use_case else None
    allowed = bool(record is not None and record.include_reasoning)
    if asked_for and not allowed:
        raise GeminiHTTPError(
            400,
            "'includeThoughts' asks for the model's reasoning, and this use case does not return "
            "it. An administrator of the use case can turn it on; it is off by default because "
            "reasoning can restate the prompt verbatim and is stored with the answer when it is "
            "on (FRD-135). Send it as false, or omit it.",
            "FAILED_PRECONDITION",
        )
    return canonical.model_copy(update={"include_reasoning": allowed}) if allowed else canonical


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
    record = await use_case_record(request, use_case)
    if record is None or not record.tools_enabled:
        raise GeminiHTTPError(
            400,
            f"Use case '{use_case}' has not enabled tool calling. An administrator of the use "
            "case can turn it on; it is off by default so that only the use cases which need "
            "functions can declare them.",
            "FAILED_PRECONDITION",
        )


def use_case_of(request: Request) -> str | None:
    """Which use case this request is attributed to, if any."""
    attribution = getattr(request.state, "attribution", None)
    slug = getattr(attribution, "use_case", None)
    return str(slug) if slug else None


async def released_for(request: Request, slug: str | None) -> list[str] | None:
    """Which models ``slug`` may call (`FRD-308`), or ``None`` for no answer.

    Three states, and each says something different:

    - ``None`` — **nobody has told us**. A request with no use case at all (an unbound break-glass
      key, `ADR-0015`), or a read-model row from a Management that predates this feature. Not ours
      to refuse: an absent *answer* is not an absent *release*, and treating it as one would stop
      every use case on a half-upgraded stack.
    - ``[]`` — somebody released nothing. That is an answer, and the answer is no.
    - a list — exactly those.

    Takes the slug rather than reading it off the request, because the **dry run** needs the same
    answer about a use case named in a body rather than resolved by attribution — and a second
    lookup written there is a second place for the three states to be collapsed into two.
    """
    record = await use_case_record(request, slug)
    if record is None:
        # The use case is not in the read-model at all. Attribution already refused an unknown one
        # where it matters; here it is the same "nothing has told us" as an older event.
        return None
    released = record.allowed_models
    return None if released is None else [str(name) for name in released]


async def released_models(request: Request) -> list[str] | None:
    """The release for *this request's* use case. Read once per request; the requirement is then
    asked per candidate, so a five-model fallback chain costs one query rather than five."""
    return await released_for(request, use_case_of(request))


async def cache_prefix_wanted(request: Request, declaration: ModelDeclaration) -> bool:
    """Whether this request's stable prefix should be marked cacheable (`FRD-133`).

    Two conditions, and the second one is deliberately **not** a dispatch condition: the use case
    has to have opted in, and the model it landed on has to be able to honour it. A model that
    cannot is served **uncached** — never skipped.

    That is the one place in this codebase where a missing capability does not skip a candidate,
    and the reason has to sit here or somebody will make it consistent with the others. Every
    other flag guards the **answer**: a model that cannot read the attachment would answer about a
    document it never saw, so the chain moves on. A model that cannot cache answers exactly the
    right thing and merely costs more. Refusing a request over a price is the opposite of what a
    fallback chain is for.

    Resolved **after** routing, because routing is what decides which model the request lands on —
    the same reason attachments, thinking and schemas are checked per hop.
    """
    if Capability.PROMPT_CACHING not in declaration.capabilities:
        return False
    use_case = getattr(request.state, "attribution", None)
    slug = getattr(use_case, "use_case", None)
    record = await use_case_record(request, slug)
    return bool(record is not None and record.prompt_caching_enabled)


async def cache_ttl_for(request: Request) -> str:
    """The lifetime this use case chose. Anything unrecognised reads as the cheap default: a typo
    must not be able to double a bill."""
    use_case = getattr(request.state, "attribution", None)
    slug = getattr(use_case, "use_case", None)
    record = await use_case_record(request, slug)
    chosen = getattr(record, "prompt_cache_ttl", "5m") if record is not None else "5m"
    return "1h" if chosen == "1h" else "5m"


async def declared_routing(request: Request) -> RoutingOf:
    """How the catalog says to reach a model (`FRD-507`): who serves it, and where.

    Handed to the dispatch chain so a candidate that was **catalogued** rather than configured
    resolves to its adapter. One function, both surfaces — the third time a step written twice
    drifted on one of them (`FRD-126`), and this one decides where a request goes.

    **The pair, not the provider alone.** One platform can host two wire formats: on Vertex,
    `google` is the Gemini dialect and `anthropic` is Anthropic's, so `vertex` identifies neither
    and a chain asking with it found nothing. Returning half of an identifier is how a lookup
    fails in a way that reads as "no such model".

    **And `addressing` with them**, for the same argument one field further along. This returned
    two thirds of the declaration's routing and the chain filled the model name from it while the
    *address* stayed as the primary's — `Routing` records what that cost. Whatever else is added to
    "how is this model reached", it belongs in the value this function returns, not beside it.
    """

    catalog = catalog_of(request)

    async def lookup(model: str) -> Routing:
        declaration = await catalog.declaration(model)
        return Routing(
            provider=declaration.provider,
            publisher=declaration.publisher,
            addressing=declaration.addressing,
        )

    return lookup


async def declared_model(request: Request) -> Callable[[str], Awaitable[ModelDeclaration]]:
    """The catalog's whole answer about a model, for a pipeline step.

    The declaration rather than the provider name, because a step needs **two** facts from the same
    place and asking twice is how they come to disagree: who serves this model, and may its
    thinking be switched off. The second is what made the first useless — with the classifier
    finally reachable, it sent an off to a model that refuses one and got a 400 it then swallowed.
    """

    catalog = catalog_of(request)

    async def lookup(model: str) -> ModelDeclaration:
        return await catalog.declaration(model)

    return lookup


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
) -> tuple[CanonicalRequest, tuple[str, ...], tuple[str, ...]]:
    """Apply the use case's pre-dispatch pipeline (FRD-300). Pass-through when none is configured.

    Returns the effective request (possibly re-routed **or rewritten**), the dispatch fallback
    chain, and any notices the caller is owed about their own request having been changed under
    them (`FRD-309`). May raise
    ``PipelineRejected`` when a filter/allow-check blocks the request — and the decisions taken up
    to that point are on the trail by then, so a blocked request records *why* rather than only
    *that* (FRD-122 FR-4).
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return canonical, (), ()
    # The engine appends into the trail's list, so a step that blocks still leaves behind the
    # decisions taken before it — including the routing that sent the request to the step that
    # refused it.
    rewrites: list[tuple[str, str]] = []
    try:
        outcome = await engine.run(
            pipeline,
            canonical,
            decisions=trail.decisions,
            model_calls=trail.model_calls,
            # Supplied for the same reason as the two lists above, and it is the one whose
            # absence *stored* something rather than losing it: a rewrite that happened before a
            # later step blocked has to reach the row of the request that was refused, or the
            # personal data the step removed is kept by the audit trail alone.
            rewrites=rewrites,
            # So a step can call a model the **catalog** knows and configuration does not
            # (`FRD-507` stage B). Without it an LLM filter fell back to the heuristic and a
            # router routed nowhere, both while the builder showed them active.
            declaration_of=await declared_model(request),
        )
    finally:
        # **One site**, and it is in the `finally` on purpose: a filter that blocked still spent
        # the tokens it took to decide that, and a use case running a blocking filter over rejected
        # traffic is paying for exactly those. Both surfaces reach it because both call this
        # function — the alternative was a hook at each surface's boundary, which is the shape that
        # let `:embedContent` slip past the pre-dispatch gate.
        await record_pipeline_calls(request, trail)
        # **The stored request is the rewritten one — whichever way the pipeline ended.** Applied
        # here rather than after the `try`, because the path that skipped it is the one where it
        # mattered most: a `pii_filter` followed by a blocking step raised past the assignment, so
        # the refusal's audit row kept the caller's original prompt — exactly the data the step
        # exists to remove, in the one place a retention clock covers and a reader can read.
        _keep_only_what_a_redactor_allows(trail, rewrites)
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
    # The rewrite reaches `trail.body` in the `finally` above, on every way out of the pipeline
    # rather than only on this one. Measured before that existed: the model was sent the redacted
    # prompt and `request_logs` kept the original, because the payload comes from the wire body
    # captured at the surface while the pipeline rewrites the canonical request — and then, once
    # this line was here and correct, the *refused* path walked past it and kept the original
    # anyway. The redaction has to protect the database as well as the model, which is the one
    # thing it is for.
    return outcome.request, outcome.fallback_models, tuple(outcome.notices)


async def run_pipeline_over_texts(
    request: Request, embed: CanonicalEmbeddingRequest, trail: AuditTrail
) -> CanonicalEmbeddingRequest:
    """Apply the steps that mean anything for texts alone (`FRD-309`, `FRD-113`).

    The sibling of :func:`run_pipeline`, and everything the two share is deliberate rather than
    copied: the same store, the same engine, the same three caller-supplied lists, and the same
    `finally` — what a step spent is recorded whether or not a later one refused, and the **stored**
    request is the rewritten one on every way out.

    What differs is what an embedding is. There is no model to route to and no answer to put a
    notice in front of, so the engine runs only `TEXT_ONLY_STEPS` and this returns the request with
    its texts replaced rather than a routed request and a fallback chain.

    Measured before this existed: one use case, one `pii_filter`, the same sentence — redacted on
    `:generateContent` and sent **and stored** untouched on `:embedContent` and on the KIRA
    surface's `/embed`. A data-protection control that the console shows as active for a use case,
    doing nothing on one of its verbs, with nothing anywhere saying so.
    """
    store: PipelineStore = request.app.state.pipeline_store
    engine: PipelineEngine = request.app.state.pipeline_engine
    use_case = getattr(getattr(request.state, "attribution", None), "use_case", None)
    pipeline = await store.get(use_case)
    if pipeline is None:
        return embed
    rewrites: list[tuple[str, str]] = []
    try:
        outcome = await engine.run_over_texts(
            pipeline,
            embed.texts,
            model=embed.model,
            decisions=trail.decisions,
            model_calls=trail.model_calls,
            rewrites=rewrites,
            declaration_of=await declared_model(request),
        )
    finally:
        await record_pipeline_calls(request, trail)
        _keep_only_what_a_redactor_allows(trail, rewrites)
    if outcome.decisions:
        _log.info(
            "pipeline_applied",
            use_case=use_case,
            model=embed.model,
            decisions=outcome.decisions,
        )
    return embed.model_copy(update={"texts": list(outcome.texts)})


def _keep_only_what_a_redactor_allows(trail: AuditTrail, rewrites: list[tuple[str, str]]) -> None:
    """What the audit row may keep of a request a `pii_filter` touched (`FRD-309` FR-3).

    Two things, in this order, and both are about the same promise — *what is stored is the
    rewritten version, and where the substitution cannot be applied the payload is dropped, never
    kept*:

    - a rewrite that happened is applied to the stored body, on **every** way out of the pipeline
      including the one where a later step refused;
    - a redaction that **failed** drops the body entirely, because there is no rewritten version
      and the original is precisely the content the step exists to remove.

    Only the first was implemented. The second was measured missing on 2026-08-27 with an
    unreachable redactor: a refused request on both `:generateContent` and `:embedContent`, nobody
    served, and the caller's name and address in `request_logs` on both rows.

    One function, called from both pipelines' `finally`, because this is a rule about the stored
    payload and the last time half of it lived at one exit the other exit kept the original.
    """
    if rewrites:
        trail.body = _rewritten_body(trail.body, rewrites)
    if redaction_failed(trail.decisions):
        trail.body = None


def _rewritten_body(
    body: dict[str, Any] | None, rewrites: list[tuple[str, str]]
) -> dict[str, Any] | None:
    """The wire body with what a step rewrote replaced — or **nothing at all**.

    A literal substitution rather than a rebuild, because the exact string that was replaced is
    known: the step has both halves. That works for either surface without either of their shapes
    being written down here.

    **If it does not match, the payload is dropped.** A body whose text could not be found is one
    this function does not understand, and storing it would store precisely the personal data the
    step removed — so the failure is losing a payload, not keeping the wrong one. `FRD-404`'s
    storage is already optional; a caller's data being kept when a use case configured a redactor
    is not.
    """
    if body is None:
        return None
    text = json.dumps(body, ensure_ascii=False)
    for before, after in rewrites:
        # **Escaped on both sides.** The prompt is looked for inside serialised JSON, so a quote or
        # a newline in it is `\"` or `\n` there rather than itself — checking the raw form and
        # substituting the escaped one dropped every prompt containing a quotation mark, which is
        # an ordinary prompt and not an exotic one.
        needle = json.dumps(before, ensure_ascii=False)[1:-1]
        if needle not in text:
            return None
        text = text.replace(needle, json.dumps(after, ensure_ascii=False)[1:-1])
    try:
        rewritten: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        return None
    return rewritten


@dataclass(slots=True)
class Prepared:
    """Everything the pre-dispatch sequence decided, handed over in one piece."""

    canonical: CanonicalRequest | None
    embed: CanonicalEmbeddingRequest | None
    fallbacks: tuple[str, ...]
    declaration: ModelDeclaration
    reservation: Reservation
    #: What the caller is told about their own request having been changed (`FRD-309`). Carried
    #: here rather than applied in the pipeline, because the pipeline runs before there is an
    #: answer to put it on — and applied in one place afterwards, so both surfaces and both exits
    #: get it by calling the same thing rather than by remembering to.
    notices: tuple[str, ...] = ()

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
    reasoning_asked_for: bool = False,
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
    # **First, before a retired use case can spend anything.** Not beside the other controls: a
    # request to a use case that no longer exists must not consume a rate-limit allowance, pay for
    # a classifier call (`FRD-125b`), or reach a model.
    await refuse_if_retired(request)
    if canonical is not None:
        # **Before anything can refuse.** It used to be set in `accounting`, which only runs once a
        # request is on its way to a model — so a request that *offered* functions and was then
        # refused (over budget, model not released, rate limited) recorded nothing about them, and
        # "somebody keeps trying to use tools here" had no answer. `declared` beside `called` is
        # what makes *offered ten and asked for none* different from *offered none* (`FRD-131`
        # FR-7), and a refusal is exactly when that difference is worth having.
        trail.tools_declared = len(canonical.tools)
        check_not_empty(canonical)
        # Before the bucket and the pipeline. A request that can never succeed should not spend
        # the caller's rate-limit allowance on the way to being refused, and it must not pay for
        # a classifier call (`FRD-125b`) either.
        await check_tools_permitted(request, canonical)
        # Beside the tools gate, and for the same reason it sits here: a request that can never
        # succeed must be refused before it spends a rate-limit allowance or pays for a classifier
        # call. `asked_for` comes from the surface, which is the only layer that knows its own
        # wire format's spelling of "give me the reasoning" (`FRD-135` FR-4).
        canonical = await resolve_reasoning(request, canonical, asked_for=reasoning_asked_for)

    units = embed.size if embed is not None else 1

    # Weighed first — a batch weighs what it is (`FRD-113` FR-6) — and then the controls that need
    # no model, before the pipeline, which can make a model call of its own.
    await guard_before_work(request, units=units)

    fallbacks: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    if canonical is not None:
        canonical, fallbacks, notices = await run_pipeline(request, canonical, trail)
        # The catalog's provider too (`FRD-507`): a model catalogued for an adapter is served by
        # it even when nobody also named it in configuration. Asking the registry alone would have
        # reported "not found" for a model an administrator had just released, which reads as a
        # typo and is a second list nobody was told to keep.
        routed = await catalog_of(request).declaration(canonical.model)
        # Publisher as well, because one platform can host two wire formats: on Vertex `google` is
        # the Gemini dialect and `anthropic` is Anthropic's, so the provider alone routes nowhere.
        if (
            registry_of(request).provider_for(canonical.model, routed.provider, routed.publisher)
            is None
        ):
            # Routing sent it somewhere nobody serves. Raised as the shared error so each surface
            # renders it in its own envelope rather than each checking for itself.
            raise GeminiHTTPError(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")
    elif embed is not None:
        # **An embedding runs the steps that are about the text** (`TEXT_ONLY_STEPS`), which since
        # 2026-08-27 is the `pii_filter`. It used to run nothing at all: this branch did not exist,
        # so a use case that had switched on redaction embedded its callers' text unredacted and
        # stored it unredacted, on a control the console shows per use case and not per verb.
        #
        # Here rather than at each embedding route, for the reason this whole function exists: a
        # rule written at a surface is a rule the next surface writes differently, and there are
        # already two of them plus a batch verb.
        embed = await run_pipeline_over_texts(request, embed, trail)

    served = canonical.model if canonical is not None else (embed.model if embed else "")
    declaration = await check_declaration(
        request, model=served, method=method, requested=requested_output
    )

    if canonical is not None:
        canonical = canonical.model_copy(
            update={
                "thinking": resolve_thinking(canonical.thinking, declaration),
                "cache_prefix": await cache_prefix_wanted(request, declaration),
                "cache_ttl": await cache_ttl_for(request),
                # What the catalogue says about reaching this model on its platform. Carried here
                # because this is the one place that has the declaration and is about to hand the
                # request to an adapter — the same reason every other resolved field is filled
                # here rather than at each surface (`FRD-126`).
                "addressing": declaration.addressing,
            }
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
            reserved_tokens(canonical.thinking, declaration)
            if canonical is not None
            else embedding_tokens(embed)
            if embed is not None
            else 0
        ),
    )
    return Prepared(canonical, embed, fallbacks, declaration, reservation, notices)


async def resolve_direct_target(
    request: Request, model: str, canonical: CanonicalRequest | None = None
) -> Upstream:
    """The adapter for a request that **cannot go through the dispatch chain** — after routing,
    and after every condition a candidate has to meet (`ADR-0012` §3).

    Two verbs are in that position and both were reaching a provider with no condition asked:

    - **a stream**, because :func:`dispatch_with_fallback` returns a finished response;
    - **an embedding**, because there is nothing to fall back *to* — a vector from a second model
      is not a substitute for a vector from the first, and the pipeline does not run for it.

    Measured on 2026-08-11 against the hermetic app. Each of these is refused by name on
    `:generateContent` and was **served with a 200** on both verbs below:

        a model no Global Administrator approved (`FRD-307`)
        a model the use case was never released (`FRD-308`)

    Residency (`FRD-115`) travels the same way, so it went with them. That is the `:embedContent`
    bypass of `FRD-405` for the third time in this codebase's history, and the reason it keeps
    recurring is that the conditions lived inside the *chain* rather than on the path every verb
    takes. They live here now, and `test_every_dispatch_applies_the_conditions.py` counts the
    verbs so a fourth one cannot be added without answering the question.

    The streaming case lost a second half, about evidence rather than authorisation: **the
    provider was resolved from the model the caller named**, before the pipeline had run. A
    `model_route` step re-targeting the request sent it to the *first* model's adapter under the
    *second* model's name — measured as an answer from server A recorded as having come from
    server B, which is precisely the claim `FRD-115` exists to make checkable. Hence ``model``
    is a parameter: a caller passes the model **routing** chose, never the one that was typed.

    ``canonical`` is the request where there is one. An embedding has none, and passing ``None``
    is not a shortcut — :func:`requirements_for` then assembles exactly the three checks that are
    properties of the *installation* rather than of the body (residency, approval, release), which
    is the correct set for a verb that carries no attachment, schema, thinking budget or tool.

    **Conditions only, no chain.** Neither verb falls back, and that is unchanged: once a stream's
    first chunk is on the wire the status is 200 and the answer has begun, and a vector from a
    different model is not a substitute for the one that was asked for. What changes is that an
    unqualified candidate is refused *before* any of that, with a status the caller can read.
    """
    permits = await requirements_for(request, canonical)
    refusal = await permits(model)
    if refusal is not None:
        # The same exception the chain raises when nothing qualifies, so every verb answers
        # `400 FAILED_PRECONDITION` with the same wording and records the same outcome.
        raise NoCapableModel([Skipped(model, refusal)])

    declared = await catalog_of(request).declaration(model)
    provider = registry_of(request).provider_for(model, declared.provider, declared.publisher)
    if provider is None:
        # `prepare_for_dispatch` already refuses this for a routed model; repeated here because
        # this function's contract is to return an adapter, and returning `None` would push the
        # decision back out to the surfaces that call it.
        raise GeminiHTTPError(404, f"Model '{model}' not found.", "NOT_FOUND")
    return provider


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
    started: float,
) -> AsyncIterator[Accounting]:
    """Hold the reservation, and account for the request **however it ends**.

    **The body is read off the trail rather than passed in.** It was a parameter, handed over by
    nine call sites, while `trail.body` held the same fact — and the moment a pipeline step began
    *rewriting* the request (`FRD-309`), the two disagreed: the rewrite reached the trail and the
    parameter kept the original, so the model was sent a redacted prompt and the audit row stored
    the personal data the step had removed. Measured on the running stack by reading a row.

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
    record = True
    # The accounting runs **inside** `hold`, not around it. Outside, `hold` sees an unresolved
    # reservation on the way out and gives it back — and then the settle books it again. One
    # request, settled once and released once, which a test caught immediately.
    async with budgets_of(request).hold(prepared.reservation):
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
    started: float,
    record: bool = True,
) -> None:
    model = state.model or prepared.model
    cost = await pricing_of(request).cost_nanos(model, state.usage)
    if not state.produced:
        # Nothing chargeable, so nothing is settled — and nothing is released here either, because
        # `hold` has already given an unresolved reservation back by the time this runs. Releasing
        # again would count the give-back twice, which a test caught within the minute.
        #
        # The rule itself stands: settling would still book one request, so a use case with a
        # request limit would lose allowance to a caller who hung up or an upstream that failed.
        cost = None
    else:
        await budgets_of(request).settle(
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
        request_payload=trail.body,
        response_payload=state.payload,
        cost_nanos=cost,
        outcome=state.outcome or Outcome.CLIENT_GONE,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        # Both exits pass through here (`FRD-126`), which is the only reason the streamed path
        # gets this for free — it did not, for an afternoon, and the row read `{"text": ""}`.
        tool_calls=tool_summary(trail),
        provenance=await provenance(request, model),
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
            cost = await pricing_of(request).cost_nanos(call.model, call.usage)
            await record_request(
                request,
                # Named for the step, so the reporting breakdown separates "what the use case
                # asked" from "what governing it cost" instead of blending them into one figure.
                operation=f"{PIPELINE_OPERATION_PREFIX}{call.step}",
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
                provenance=await provenance(request, call.model),
                # From the trail, not from a default. `record_request` used to default this to
                # `"gemini"`, which made a call site that forgot it right on one surface and wrong
                # on the other — measured as a KIRA request's classifier row filed under `gemini`,
                # so a use case's *governance* spend was reported against a surface it never used.
                api=trail.api,
            )
            await budgets_of(request).book_side_call(
                getattr(attribution, "use_case", None),
                # The person, like every other booking against a per-head allowance.
                getattr(attribution, "person", None),
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


def withheld_because(response: CanonicalResponse, *, structured: bool) -> str:
    """Why the notice cannot go in front of this answer, or ``""`` when it can (`FRD-309`).

    **Text only, and that is a rule rather than a shortcut.** Three ways an answer is not text to
    put a sentence in front of, and each was written into `with_notices`' docstring from the
    beginning. Only two of them were ever implemented.

    - **A structured answer.** A response constrained by a ``responseSchema`` is a document the
      caller parses, and a sentence in front of it makes the document invalid — they get a parse
      error instead of an answer, which is worse than not being told. The condition read
      ``not response.text.strip() or response.tool_calls``, and a JSON document is neither of
      those: it is non-empty text with no tool call, so the notice **was** prepended and the
      document **was** broken. The docstring said the opposite, which is why it was not noticed —
      the check needs a fact about the *request*, and the function was only ever handed the
      response.
    - **A tool call.** The answer *is* the call; there is no prose.
    - **No text at all.**

    Returning the reason rather than a boolean is what lets the audit row say *which* of the three
    happened, instead of one message for all of them.
    """
    if structured:
        return "the answer is a document the caller parses, and a sentence would invalidate it"
    if response.tool_calls:
        return "the answer is a tool call, which carries no text"
    if not response.text.strip():
        return "the answer carries no plain text to put it in front of"
    return ""


def with_notices(
    response: CanonicalResponse, notices: tuple[str, ...], *, structured: bool = False
) -> CanonicalResponse:
    """Prepend what the caller is owed about their own request having been changed (`FRD-309`).

    ``structured`` says whether the caller constrained the answer to a schema — see
    :func:`withheld_because`, which owns the rule.
    """
    if not notices or withheld_because(response, structured=structured):
        return response
    return response.model_copy(update={"text": "\n\n".join([*notices, response.text])})


def notice_outcome(
    response: CanonicalResponse, notices: tuple[str, ...], *, structured: bool = False
) -> dict[str, Any] | None:
    """What the audit row says about the notice — including that it was **not** shown, and why."""
    if not notices:
        return None
    why = withheld_because(response, structured=structured)
    return {"step": "notice", "action": "withheld" if why else "shown", "why": why}


def annotate(
    canonical: CanonicalRequest | None,
    response: CanonicalResponse,
    prepared: Prepared,
    trail: AuditTrail,
) -> CanonicalResponse:
    """Apply `FRD-309`'s notice to a finished answer and record what became of it.

    **One site, every non-streamed exit**, which is what `with_notices` claimed in its own docstring
    — *"called from every exit, for the reason `FRD-128` gives: a fact applied at each `return` is a
    fact eventually missing from one of them"* — and was not: a grep on 2026-08-15 found exactly one
    production caller, Gemini's `:generateContent`. The KIRA surface's `/chat` and both streams
    applied nothing, so a use case running a `pii_filter` rewrote its callers' prompts and told
    three quarters of them nothing, while the builder showed the notice as configured. The audit
    row was silent about it too, so "no notice shown" and "nothing was redacted" were
    indistinguishable — the exact pair `notice_outcome` exists to keep apart.

    Streams cannot use this: by the time there is a finished answer their first chunk is on the
    wire. They use :class:`StreamedNotice`, which applies the same rule one chunk earlier.
    """
    # **Where it was produced, before anything else may return early.** The adapter is the only
    # layer that knows which region answered — with a chain, the catalogue's first entry is a guess
    # — and this is the one site every non-streamed exit passes through.
    if response.served_region:
        trail.served_region = response.served_region
    if not prepared.notices:
        return response
    structured = canonical is not None and canonical.response_schema is not None
    note = notice_outcome(response, prepared.notices, structured=structured)
    if note is not None:
        trail.decisions.append(note)
    return with_notices(response, prepared.notices, structured=structured)


class StreamedNotice:
    """`FRD-309`'s notice on a **streamed** answer: in front of the first text that arrives.

    A stream has no finished answer to prefix — by the time one exists, the first chunk has been
    sent — so the notice leads the answer instead of following it. That is also the better reading
    order: the caller is told their prompt was changed *before* they read what it produced.

    The rule :func:`withheld_because` states is preserved exactly, and two thirds of it are
    preserved by construction rather than by a second condition:

    - a **structured** answer is refused up front, because the caller will parse it;
    - a **tool call** produces no text delta, so :meth:`lead` is never asked and nothing is shown;
    - an answer with **no text at all** is the same case.

    :meth:`outcome` says afterwards which of those happened, so the audit row of a streamed request
    carries the same fact as a buffered one.
    """

    def __init__(self, notices: tuple[str, ...], *, structured: bool = False) -> None:
        self._notices = () if structured else tuple(notices)
        self._configured = tuple(notices)
        self._structured = structured
        self._shown = False

    def lead(self, text_delta: str) -> str:
        """The delta to send: the first non-empty one carries the notice, the rest are unchanged."""
        if not self._notices or not text_delta:
            return text_delta
        led = "\n\n".join([*self._notices, text_delta])
        self._notices = ()
        self._shown = True
        return led

    def outcome(self) -> dict[str, Any] | None:
        """What the audit row says about it, or ``None`` when there was no notice to show."""
        if not self._configured:
            return None
        if self._shown:
            return {"step": "notice", "action": "shown", "why": ""}
        why = (
            "the answer is a document the caller parses, and a sentence would invalidate it"
            if self._structured
            else "the answer carries no plain text to put it in front of"
        )
        return {"step": "notice", "action": "withheld", "why": why}
