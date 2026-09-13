"""Request-scoped lookups every surface shares: attribution, the catalogue, the use case's record.

Results are memoised on `request.state`, so every reader in one request sees the same answer and
the same model or use case is read once rather than once per caller (see
`test_a_request_costs_a_bounded_number_of_reads.py`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from aira_common.models import Capability
from aira_gateway.auth.attribution import Attribution
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.core.schema import SchemaBounds
from aira_gateway.db.models import UseCaseRead
from aira_gateway.embedding import EmbeddingBounds
from aira_gateway.pipeline.dispatch import Routing, RoutingOf
from aira_gateway.state import providers_of, sessionmaker_of, settings_of
from aira_gateway.upstreams.base import UpstreamModel

#: The verbs of a model that generates. The embedding verbs follow the declaration's batch flag.
_GENERATION_VERBS = ("generateContent", "streamGenerateContent")


def attribution_of(request: Request) -> Attribution | None:
    """Who this request is attributed to, once authentication has resolved it."""
    attribution: Attribution | None = getattr(request.state, "attribution", None)
    return attribution


def use_case_of(request: Request) -> str | None:
    """Which use case this request is attributed to, if any."""
    slug = getattr(attribution_of(request), "use_case", None)
    return str(slug) if slug else None


def catalog_of(request: Request) -> ModelCatalog:
    """The catalogue, as **this request's** view of it (`ModelCatalog.per_request`)."""
    memoised: ModelCatalog | None = getattr(request.state, "catalog", None)
    if memoised is None:
        source: ModelCatalog = request.app.state.catalog
        memoised = source.per_request()
        request.state.catalog = memoised
    return memoised


async def use_case_record(request: Request, slug: str | None) -> UseCaseRead | None:
    """A use case's read-model row, read **once** per request.

    Per request, never per app: the row carries the release and the storage switches, and a stale
    copy would keep applying a control after somebody changed it. ``None`` is cached too, so one
    request cannot see both "unknown" and a row.
    """
    if not slug:
        return None
    seen: dict[str, UseCaseRead | None] = getattr(request.state, "use_cases", None) or {}
    if slug not in seen:
        async with sessionmaker_of(request)() as session:
            seen[slug] = await session.get(UseCaseRead, slug)
        request.state.use_cases = seen
    return seen[slug]


async def released_for(request: Request, slug: str | None) -> list[str] | None:
    """Which models ``slug`` may call (`FRD-308`) — three states, each meaning something different.

    - ``None``: nobody has told us (no use case, or a row from a Management predating the feature).
      An absent answer is not an absent release; refusing here would stop a half-upgraded stack.
    - ``[]``: somebody released nothing, and the answer is no.
    - a list: exactly those.

    Takes the slug so the dry run can ask about a use case named in a body.
    """
    record = await use_case_record(request, slug)
    if record is None:
        return None
    released = record.allowed_models
    return None if released is None else [str(name) for name in released]


async def released_models(request: Request) -> list[str] | None:
    """The release for *this request's* use case, read once and then asked per candidate."""
    return await released_for(request, use_case_of(request))


async def declared_routing(request: Request) -> RoutingOf:
    """How the catalogue says to reach a model (`FRD-507`): provider, publisher and addressing.

    All three together: one platform hosts several wire formats (Vertex serves `google` and
    `anthropic`), so the provider alone identifies nothing, and a partial answer fails as
    "no such model".
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
    """The catalogue's whole declaration of a model, for a pipeline step.

    The declaration rather than single fields, because a step needs several facts from the same
    place (who serves it, whether its thinking may be switched off) and two lookups can disagree.
    """
    catalog = catalog_of(request)

    async def lookup(model: str) -> ModelDeclaration:
        return await catalog.declaration(model)

    return lookup


async def provenance(
    request: Request, model: str, served_region: str = ""
) -> tuple[str, str, str] | None:
    """``(provider, publisher, region)`` for the audit row (`FRD-115`).

    ``served_region`` wins whenever the adapter reported one (`FRD-609`): with a failover chain the
    configured region is not where the request went, and a wrong residency claim reads as
    evidence. Otherwise the registry answers, then — for a model reached only through the
    catalogue (`FRD-507`) — the adapter owning its provider **and publisher**, with the model's own
    declared publisher and first region before the adapter's, which belong to another model.
    ``None`` rather than blank fields.
    """
    registry = providers_of(request)
    described = registry.get_model(model)
    if described is not None and described.provider:
        return (described.provider, described.publisher, served_region or described.region)

    declared = await catalog_of(request).declaration(model)
    if not declared.provider:
        return None
    configured = registry.provenance_for(declared.provider, declared.publisher)
    if configured is None:
        return None
    region = served_region or next(iter(declared.regions), "") or configured[2]
    return (configured[0], declared.publisher or configured[1], region)


async def served_models(request: Request) -> list[UpstreamModel]:
    """Every model a request may name, with the verbs the catalogue lets it answer.

    Configuration **and** the catalogue: a catalogued model is served by the adapter owning its
    provider even when configuration does not name it (`FRD-507`), so a list of configuration alone
    omits models that work. An unapproved one is left out, since it would be refused (`FRD-307`).
    """
    registry = providers_of(request)
    catalog = catalog_of(request)
    listed = [
        UpstreamModel(
            model.name,
            model.version,
            _verbs(await catalog.declaration(model.name), model.supported_methods),
            model.provider,
            model.publisher,
            model.region,
        )
        for model in registry.models()
    ]
    configured = {model.name for model in listed}
    for declaration in await catalog.catalogued():
        if declaration.name in configured or not declaration.approved or not declaration.provider:
            continue
        adapter = registry.provider_for(
            declaration.name, declaration.provider, declaration.publisher
        )
        if adapter is None:
            continue
        listed.append(
            UpstreamModel(
                declaration.name,
                declaration.name,
                _verbs(declaration, (*_GENERATION_VERBS, "embedContent")),
                declaration.provider,
                declaration.publisher,
                declaration.regions[0] if declaration.regions else "",
            )
        )
    return listed


def _verbs(declaration: ModelDeclaration, offered: tuple[str, ...]) -> tuple[str, ...]:
    """The verbs an adapter offers, narrowed to what the model is declared to do — an undeclared
    model has the baseline — plus the batch verb where the declaration allows one."""
    allowed: set[str] = set()
    if declaration.can(Capability.GENERATE):
        allowed.update(_GENERATION_VERBS)
    if declaration.can(Capability.EMBED):
        allowed.add("embedContent")
        if declaration.supports_batch:
            allowed.add("batchEmbedContents")
    verbs = [verb for verb in offered if verb in allowed]
    if "embedContent" in verbs and "batchEmbedContents" in allowed - set(verbs):
        verbs.append("batchEmbedContents")
    return tuple(verbs)


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
