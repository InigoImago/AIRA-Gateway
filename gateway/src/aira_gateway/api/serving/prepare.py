"""The pre-dispatch sequence (`FRD-126`), in the one place that owns its order.

Every guarantee this layer makes is a guarantee about order:

    rate limit before the pipeline   or a refused request pays for a classifier call
    declaration after routing        or a cap is checked against a model that never serves it
    thinking after routing           or a budget is validated against the wrong model
    reservation last                 or it is made against the model the caller *named*

A surface parses its wire format and calls :func:`prepare_for_dispatch`; it never calls the steps
itself (`test_surface_layering.py`). Verbs that cannot use the dispatch chain — streams and
embeddings — resolve their adapter through :func:`resolve_direct_target`, which applies the same
conditions (`test_every_dispatch_applies_the_conditions.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from aira_common.observability import set_span_attributes
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving.conditions import installation_requirements
from aira_gateway.api.serving.context import (
    catalog_of,
    embedding_bounds,
    use_case_of,
)
from aira_gateway.api.serving.controls import (
    cache_prefix_wanted,
    cache_ttl_for,
    check_attachment_bounds,
    check_declaration,
    check_not_empty,
    check_sampling,
    check_tools_permitted,
    enforce_pre_dispatch,
    guard_before_work,
    prompt_texts,
    refuse_unless_live,
    resolve_reasoning,
)
from aira_gateway.api.serving.pipeline_run import run_pipeline, run_pipeline_over_texts
from aira_gateway.audit import AuditTrail
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalEmbeddingRequest, CanonicalRequest
from aira_gateway.embedding import check_shape as check_embedding_shape
from aira_gateway.embedding import validate as validate_embedding
from aira_gateway.explaining import TRAIL_STATE
from aira_gateway.pipeline.dispatch import Fit, NoCapableModel, Permits, Skipped
from aira_gateway.requirements import (
    MediaTypesSupported,
    OutputCapAccepted,
    SamplingExpressible,
    SchemaExpressible,
    SpeechSupported,
    StructuredOutputSupported,
    ThinkingHonoured,
    ToolsSupported,
    permits,
)
from aira_gateway.state import providers_of
from aira_gateway.thinking import ThinkingRejected, check_budget_fits, reserved_tokens
from aira_gateway.thinking import resolve as resolve_thinking
from aira_gateway.upstreams.base import Upstream


@dataclass(slots=True)
class Prepared:
    """Everything the pre-dispatch sequence decided, handed over in one piece."""

    canonical: CanonicalRequest | None
    embed: CanonicalEmbeddingRequest | None
    fallbacks: tuple[str, ...]
    declaration: ModelDeclaration
    reservation: Reservation
    #: What the caller is told about their request having been changed (`FRD-309`). Carried here
    #: because the pipeline runs before there is an answer; applied once, by `answers.annotate`.
    notices: tuple[str, ...] = ()
    #: The request as the caller made it — after the pipeline, before any model's defaults. What a
    #: chain starts from and its conditions ask about; ``canonical`` is this fitted to the model
    #: routing chose, and every other candidate is fitted the same way (`fitting`).
    asked: CanonicalRequest | None = None

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
    reasoning_asked_for: bool | None = None,
    embed: CanonicalEmbeddingRequest | None = None,
    requested_output: int | None = None,
    default_task_type: str | None = None,
) -> Prepared:
    """Run the whole pre-dispatch sequence, in order (see the module docstring)."""
    # Kept where the response can read it: its headers say which model answered and what the
    # pipeline did (`FRD-628` FR-2), from this trail, whichever surface and exit answers.
    setattr(request.state, TRAIL_STATE, trail)
    # First: a use case that is unknown here or retired must not spend an allowance or reach a
    # classifier.
    await refuse_unless_live(request, use_case_of(request))
    if canonical is not None:
        # Recorded before anything can refuse, so a refused request still shows it offered tools.
        trail.tools_declared = len(canonical.tools)
        # Requests that can never succeed are refused before they spend an allowance.
        check_not_empty(canonical)
        check_attachment_bounds(canonical)
        check_sampling(canonical)
        await check_tools_permitted(request, canonical)
        canonical = await resolve_reasoning(request, canonical, asked_for=reasoning_asked_for)
    if embed is not None:
        # The same rule for an embedding: an empty or oversized batch is refused before the rate
        # limit takes its weight, or one caller's unservable batch spends the use case's allowance.
        check_embedding_shape(embed, embedding_bounds(request))

    # An embedding batch weighs one request per text (`FRD-113` FR-6).
    units = embed.size if embed is not None else 1
    await guard_before_work(request, units=units)

    fallbacks: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    if canonical is not None:
        canonical, fallbacks, notices = await run_pipeline(request, canonical, trail)
        # The catalogue's provider and publisher too (`FRD-507`): a catalogued model is served by
        # its adapter even when configuration does not name it.
        routed = await catalog_of(request).declaration(canonical.model)
        if (
            providers_of(request).provider_for(canonical.model, routed.provider, routed.publisher)
            is None
        ):
            raise GeminiHTTPError(404, f"Model '{canonical.model}' not found.", "NOT_FOUND")
    elif embed is not None:
        embed = await run_pipeline_over_texts(request, embed, trail)

    served = canonical.model if canonical is not None else (embed.model if embed else "")
    declaration = await check_declaration(
        request,
        model=served,
        method=method,
        requested=requested_output,
        speech=canonical is not None and canonical.speech is not None,
    )

    asked = canonical
    if canonical is not None:
        canonical = await fitted(request, canonical, declaration)
        # The resolved budget against the cap that will be sent: a budget that fills the
        # allowance it is drawn from is the caller's `400`, not the provider's refusal.
        check_budget_fits(canonical.thinking, canonical.max_output_tokens)
    if embed is not None:
        embed = validate_embedding(
            embed, declaration, embedding_bounds(request), default_task_type=default_task_type
        )
        # The catalogue's addressing, as for generation: a catalogued model on a regional
        # platform has nowhere to be sent without it (`FRD-507`).
        embed = embed.model_copy(update={"addressing": declaration.addressing})

    reservation = await enforce_pre_dispatch(
        request,
        model=served,
        max_output_tokens=requested_output,
        attachments=[part.media_type for part in canonical.attachments] if canonical else None,
        units=units,
        input_texts=(
            prompt_texts(canonical) if canonical is not None else embed.texts if embed else ()
        ),
        extra_tokens=reserved_tokens(canonical.thinking, declaration) if canonical else 0,
    )
    # Last, so the span describes what will actually be dispatched, not what the caller sent.
    describe_on_the_span(canonical, embed)
    return Prepared(canonical, embed, fallbacks, declaration, reservation, notices, asked)


async def fitted(
    request: Request, asked: CanonicalRequest, declaration: ModelDeclaration
) -> CanonicalRequest:
    """``asked``, as it is sent to the model ``declaration`` describes.

    The caller's own settings stay the caller's. What the caller left to the model is that model's:
    the output cap it declares as its default (`FRD-114` FR-2), its default thinking (`FRD-111`),
    the cache marker it can honour (`FRD-133`) — and its addressing. Asked of the model routing
    chose by `prepare_for_dispatch` and of every other candidate by `fitting`, so a fallback is
    never sent the defaults of the model in front of it.
    """
    return asked.model_copy(
        update={
            "thinking": resolve_thinking(asked.thinking, declaration),
            "cache_prefix": await cache_prefix_wanted(request, declaration),
            "cache_ttl": await cache_ttl_for(request),
            "addressing": declaration.addressing,
            # The model's default when the caller set none, so the cap that applies is the one
            # this installation declared, not the vendor's.
            "max_output_tokens": declaration.output_cap(asked.max_output_tokens),
        }
    )


def fitting(request: Request) -> Fit:
    """`fitted` for each candidate of a chain, or why the candidate cannot be sent the request.

    What is the caller's `400` for the model routing chose — a thinking setting it does not offer,
    a budget that does not fit inside its output cap — is a reason to pass over any other
    candidate: the request was valid where it was addressed, and another candidate may take it.
    """
    catalog = catalog_of(request)

    async def fit(addressed: CanonicalRequest) -> CanonicalRequest | str:
        declaration = await catalog.declaration(addressed.model)
        try:
            candidate = await fitted(request, addressed, declaration)
            check_budget_fits(candidate.thinking, candidate.max_output_tokens)
        except ThinkingRejected as exc:
            return exc.message
        return candidate

    return fit


async def requirements_for(request: Request, canonical: CanonicalRequest | None) -> Permits:
    """What a candidate must satisfy to serve this request (`ADR-0012` §3).

    Residency, approval (`FRD-307`), the use case's release (`FRD-308`) and a price where spend is
    capped always apply — they are properties of the installation, and a pipeline step asks them
    too (`conditions.installation_requirements`). The rest only when the request asks for that
    feature.

    ``canonical`` is the request **as the caller made it** (`Prepared.asked`): a condition is about
    what was asked for, and a default the model in front of a candidate declares was not.
    """
    registry = providers_of(request)
    catalog = catalog_of(request)
    checks = await installation_requirements(request)
    if canonical is not None and canonical.media_types:
        checks.append(MediaTypesSupported(catalog, canonical.media_types))
    if canonical is not None and canonical.response_schema is not None:
        # Whether the *model* offers structured output and whether the *dialect* can carry this
        # schema are separate questions (`ADR-0011` rule 3).
        checks.append(StructuredOutputSupported(catalog))
        checks.append(SchemaExpressible(registry, canonical.response_schema, catalog))
    if canonical is not None and canonical.thinking is not None:
        checks.append(ThinkingHonoured(catalog, canonical.thinking))
    if canonical is not None and canonical.max_output_tokens is not None:
        checks.append(OutputCapAccepted(catalog, canonical.max_output_tokens))
    if canonical is not None and canonical.sampling_requested:
        checks.append(SamplingExpressible(registry, canonical.sampling_requested, catalog))
    if canonical is not None and canonical.tools:
        checks.append(ToolsSupported(catalog))
    if canonical is not None and canonical.speech is not None:
        checks.append(SpeechSupported(registry, catalog))
    return permits(checks)


async def resolve_direct_target(
    request: Request, model: str, canonical: CanonicalRequest | None = None
) -> Upstream:
    """The adapter for a verb that **cannot use the dispatch chain**, with every condition applied.

    A stream cannot fall back once its first chunk is sent, and an embedding has nothing to fall
    back to (another model's vector is no substitute). Both must still meet the conditions a chain
    candidate meets — otherwise unapproved and unreleased models answer 200 on exactly these verbs.

    ``model`` is the model **routing** chose, never the one typed, so the audit names the adapter
    that actually served. ``canonical`` is ``None`` for an embedding, which selects only the
    installation-level checks.
    """
    permits = await requirements_for(request, canonical)
    refusal = await permits(model)
    if refusal is not None:
        # The chain's own exception, so every verb answers and records alike.
        raise NoCapableModel([Skipped(model, refusal)])

    declared = await catalog_of(request).declaration(model)
    provider = providers_of(request).provider_for(model, declared.provider, declared.publisher)
    if provider is None:
        raise GeminiHTTPError(404, f"Model '{model}' not found.", "NOT_FOUND")
    return provider


def describe_on_the_span(
    canonical: CanonicalRequest | None, embed: CanonicalEmbeddingRequest | None
) -> None:
    """What this request *is*, on its span (`FRD-110`, `FRD-112`, `FRD-113` §9).

    Absent rather than zero: `set_span_attributes` drops `None`, so an unresolved value leaves no
    attribute. The embedding figures are the resolved ones — what was sent, not what was typed.
    """
    attributes: dict[str, object] = {}
    if canonical is not None:
        attributes["aira.request.parts"] = sum(len(message.parts) for message in canonical.messages)
        attachments = canonical.attachments
        if attachments:
            attributes["aira.request.attachment_bytes"] = sum(
                len(part.data) for part in attachments
            )
        if canonical.response_schema is not None:
            attributes["aira.response_schema"] = True
            # The schema's own fingerprint, so the span and anything else that names a schema
            # name it alike (`FRD-112` §6).
            attributes["aira.response_schema.digest"] = canonical.response_schema.digest()
    if embed is not None:
        attributes["aira.embedding.batch_size"] = embed.size
        attributes["aira.embedding.task_type"] = embed.task_type
        attributes["aira.embedding.dimensions"] = embed.dimensions
    set_span_attributes(attributes)
