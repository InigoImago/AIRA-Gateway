"""The chain a request falls back along, by its kind, and what holds each candidate to it.

`FRD-310`, `ADR-0048`.

**Which chain.** The installation keeps one per kind of request — text generation, embedding,
ranking — and the use case decides what to do with them (`aira_common.fallback.FallbackMode`): the
installation's, its own for text generation, or none. Every candidate still meets every condition
at every hop — the use case's release among them — so a chain the installation set reaches only
the models each use case may call.

**What holds a candidate to the request.** A text answer is held by `requirements_for`; a set of
vectors and a ranking by the two fits below, because what they need of a stand-in is not a
condition on the model alone:

- **an embedding** goes only to a model that gives vectors of the width the caller's index takes —
  the width the model asked for would have given — and honours the task type and the batch as that
  model would. Vectors of two models are never comparable, so an installation that sets an
  embedding chain is told so where it sets it; this rule keeps a stand-in from also breaking the
  index's shape;
- **a ranking** goes only to a reranker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from aira_common.fallback import FallbackKind
from aira_gateway.api.serving.conditions import installation_requirements
from aira_gateway.api.serving.context import attribution_of, catalog_of, embedding_bounds
from aira_gateway.core.canonical import CanonicalEmbeddingRequest, CanonicalRerankRequest
from aira_gateway.embedding import EmbeddingRejected
from aira_gateway.embedding import validate as validate_embedding
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.dispatch import Permits
from aira_gateway.pipeline.store import FallbackChains, PipelineStore
from aira_gateway.requirements import (
    EmbeddingSupported,
    GenerationSupported,
    RankingSupported,
    permits,
)
from aira_gateway.state import providers_of


async def fallbacks_for(
    request: Request, kind: FallbackKind, pipeline: Pipeline | None = None, *, read: bool = True
) -> tuple[str, ...]:
    """The models a request of ``kind`` falls back to, in order.

    ``pipeline`` is the use case's, where the caller already holds it; otherwise it is read here
    (``read``). A use case with no pipeline follows the installation's chains, which are empty —
    no fallback — until somebody sets one.
    """
    if pipeline is None and read:
        store: PipelineStore = request.app.state.pipeline_store
        pipeline = await store.get(getattr(attribution_of(request), "use_case", None))
    chains: FallbackChains = request.app.state.fallback_chains
    installation = await chains.chain(kind)
    if pipeline is None:
        return installation
    return pipeline.chain_for(kind, installation)


async def step_requirements(request: Request) -> Permits:
    """What a pipeline step's model must meet: the installation's conditions (`conditions`), and
    text generation — every step asks its model for text (`FRD-310`)."""
    checks = await installation_requirements(request)
    checks.append(GenerationSupported(catalog_of(request)))
    return permits(checks)


async def embedding_requirements(request: Request) -> Permits:
    """What a model must meet to give an embedding's vectors, at every hop of its chain."""
    checks = await installation_requirements(request)
    checks.append(EmbeddingSupported(catalog_of(request)))
    return permits(checks)


async def ranking_requirements(request: Request) -> Permits:
    """What a model must meet to rank a request's records, at every hop of its chain."""
    checks = await installation_requirements(request)
    checks.append(RankingSupported(providers_of(request), catalog_of(request)))
    return permits(checks)


def embedding_fit(
    request: Request, asked: CanonicalEmbeddingRequest
) -> Callable[[CanonicalEmbeddingRequest], Awaitable[CanonicalEmbeddingRequest | str]]:
    """Each candidate of an embedding's chain, as it is sent ``asked`` — or why it cannot be.

    ``asked`` is the request as the model it named validated it: its task type, and its width —
    that model's default where the caller set none. The model asked for is sent it as it is. Any
    other is held to it: the same width, or the caller's index cannot take the vectors at all; the
    task type and the batch, as `embedding.validate` holds every model to them. A width the model
    asked for does not declare cannot be shown to match, so then no other model may answer. Texts
    that carry the task as a prompt (`FRD-146` FR-7) are read as meant only by the model they were
    written for.
    """
    catalog = catalog_of(request)
    bounds = embedding_bounds(request)

    async def fit(addressed: CanonicalEmbeddingRequest) -> CanonicalEmbeddingRequest | str:
        if addressed.model == asked.model:
            return addressed
        if asked.dimensions is None:
            return (
                f"the width of '{asked.model}''s vectors is not declared, so no other model can "
                "be shown to give vectors of that width"
            )
        declaration = await catalog.declaration(addressed.model)
        if (
            asked.task_in_text
            and declaration.wire_name != (await catalog.declaration(asked.model)).wire_name
        ):
            return f"the texts carry their task the way '{asked.model}' reads it, as a prompt"
        try:
            fitted = validate_embedding(addressed, declaration, bounds)
        except EmbeddingRejected as exc:
            return exc.message
        return fitted.model_copy(update={"addressing": declaration.addressing})

    return fit


def ranking_fit(
    request: Request,
) -> Callable[[CanonicalRerankRequest], Awaitable[CanonicalRerankRequest | str]]:
    """Each candidate of a ranking's chain, sent the records at its own address."""
    catalog = catalog_of(request)

    async def fit(addressed: CanonicalRerankRequest) -> CanonicalRerankRequest | str:
        declaration = await catalog.declaration(addressed.model)
        return addressed.model_copy(update={"addressing": declaration.addressing})

    return fit
