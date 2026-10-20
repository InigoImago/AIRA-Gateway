"""What a model must satisfy to serve a particular request (ADR-0012 §3).

The dispatch chain asks one question per candidate — *may this model serve this request?* — and
each requirement here answers it in words an operator can act on. Every check runs against the
model **about to be dispatched to**, at every hop: with routing and cross-vendor fallback that is
not the model the caller named, and a check that runs before routing protects nothing.

They all guard one failure: **a chain must not degrade a request silently.** Each property changes
what comes back without changing the status code, so a candidate that cannot meet one is skipped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol

from aira_common.models import Capability
from aira_gateway.catalog import ModelCatalog
from aira_gateway.core.canonical import CanonicalRequest, Thinking
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.residency import RegionNotAllowed, check_region
from aira_gateway.thinking import permitted_by
from aira_gateway.upstreams.base import ProviderRegistry

#: The values that leave a model's sampling as it is: no scaling, the whole distribution. A model
#: that samples at its own settings accepts these and nothing else (`SamplingAccepted`).
NEUTRAL_TEMPERATURE = 1.0
NEUTRAL_TOP_P = 1.0


class Requirement(Protocol):
    """Answers why a model may not serve this request, or ``None`` if it may."""

    async def refusal(self, model: str) -> str | None: ...


class Pricing(Protocol):
    """The price question, as `PricingService` answers it.

    A protocol rather than the class: this module is the request path's rule book and importing the
    service would make it depend on the database layer for one method.
    """

    async def price_for(self, model: str) -> object | None: ...


def _is_test_double(registry: object | None, model: str) -> bool:
    """Whether ``model`` is served by the mock, which is not a model.

    Governing deterministic fiction is theatre; the exemption is bounded by `create_app`
    registering the double in no environment but `local` and the demo.
    """
    provider = (
        registry.provider_for(model)  # type: ignore[attr-defined]
        if registry is not None
        else None
    )
    return bool(getattr(provider, "is_test_double", False))


async def adapter_for(
    registry: ProviderRegistry, catalog: ModelCatalog, model: str
) -> object | None:
    """The adapter that will actually serve ``model`` — configuration **or** catalogue.

    The dialect requirements must reach the adapter the dispatch chain will. A lookup by name alone
    answers ``None`` for a model servable because it is catalogued (`FRD-507` stage B), and reading
    that as "no restriction" skips the check for exactly those models.
    """
    declaration = await catalog.declaration(model)
    return registry.provider_for(model, declaration.provider, declaration.publisher)


class RegionAllowed:
    """The model must run somewhere this request is permitted to be processed.

    Today the permitted set is the deployment's allow-list, already checked at startup; this
    enforces it **per hop**, which is what a per-use-case residency will need. A model with no
    declared region is not refused — the mock and the laptop adapter declare none.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        allowed: Sequence[str],
        for_models: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._registry = registry
        self._allowed = tuple(allowed)
        #: Regions single models are permitted by name (`AIRA_ALLOWED_MODEL_REGIONS`).
        self._for_models = for_models or {}

    async def refusal(self, model: str) -> str | None:
        if not self._allowed:
            return None  # no residency constraint configured for this request
        described = self._registry.get_model(model)
        if described is None:
            return None  # not ours to refuse; the chain reports it as unserved
        if not described.region:
            return None  # no residency posture to violate
        try:
            # The policy's own rule, exceptions by name included, rather than a second copy of it.
            check_region(described.region, self._allowed, model=model, for_models=self._for_models)
        except RegionNotAllowed:
            return (
                f"runs in '{described.region}', and this request may only be processed in "
                f"{sorted(self._allowed)}"
            )
        return None


class PricedWhereCostIsLimited:
    """A model with no price may not serve a request a **cost** limit binds (`FRD-610` §3.2).

    The hole this closes: an unpriced request contributes nothing to a spend figure. It is counted
    apart as `unpriced_requests` — right for reporting, where unknown is not zero — and for
    *enforcement* unknown becomes **unbounded**, so a use case whose models are all unpriced spends
    without limit against a budget that looks configured.

    Refusing makes a price a precondition for governance, which is the honest relation between
    them. The alternative considered and rejected was a worst-case ceiling used for enforcement
    only: it invents a number, and a wrong number that permits spending is worse than a refusal
    that stops it (`ADR-0021` spent a day removing invented numbers).

    **A requirement rather than a check at the reservation**, because that is what lets a chain do
    something better than refuse: an unpriced candidate is skipped by name and a priced one serves
    (`FRD-302`). A request whose *only* candidate is unpriced is refused with the reason.

    Ordered cheaply: the price is asked first, and the budgets only when there is no price. A
    normal request therefore pays nothing for this check.
    """

    def __init__(
        self,
        pricing: Pricing,
        cost_limited: Callable[[], Awaitable[bool]],
        registry: object | None = None,
    ) -> None:
        self._pricing = pricing
        self._cost_limited = cost_limited
        self._registry = registry

    async def refusal(self, model: str) -> str | None:
        if _is_test_double(self._registry, model):
            return None
        if await self._pricing.price_for(model) is not None:
            return None
        if not await self._cost_limited():
            return None
        return (
            f"'{model}' has no price on file and this request is subject to a spend limit. An "
            "unpriced request cannot be counted against a cost budget, so it would spend without "
            "bound. A Global Administrator adds the price under Models — or the installation "
            "accepts the gap by setting AIRA_ENFORCE_PRICED_MODELS=false."
        )


class ModelApproved:
    """A Global Administrator must have catalogued and released this model (`FRD-307`).

    A model appearing on an upstream is not the same event as somebody accepting it into this
    installation. **A model not in the catalog is refused too** — the owner's narrowing of `FRD-114`
    FR-7, so deleting a declaration cannot make a model usable again. The two refusals stay apart:
    one needs somebody to add the model, the other to release it.
    """

    def __init__(self, catalog: ModelCatalog, registry: object | None = None) -> None:
        self._catalog = catalog
        self._registry = registry

    async def refusal(self, model: str) -> str | None:
        if _is_test_double(self._registry, model):
            return None

        declaration = await self._catalog.declaration(model)
        if not declaration.in_catalog:
            return (
                f"'{model}' is not in the model catalog. Only models a Global Administrator has "
                "catalogued and approved may be used."
            )
        if declaration.approved:
            return None
        return (
            f"'{model}' is in the catalog and has not been approved for use. A Global "
            "Administrator releases a model before a use case may call it."
        )


class ModelReleasedForUseCase:
    """This use case must have been given the model (`FRD-308`).

    The second gate, owned by the use case's administrator; `ModelApproved` is the installation's.
    Three states: a list is exactly those, **empty means none**, and `None` means no event has said
    — a read-model row from an older Management, which must not stop every use case on a partially
    upgraded stack. A requirement rather than a pipeline step because a route or a fallback could
    otherwise reach a model the use case was never given.
    """

    def __init__(
        self,
        released: Sequence[str] | None,
        use_case: str | None,
        registry: object | None = None,
    ) -> None:
        self._released = None if released is None else frozenset(released)
        self._use_case = use_case
        self._registry = registry

    async def refusal(self, model: str) -> str | None:
        if self._released is None:
            return None  # nothing has told us; not ours to refuse
        if _is_test_double(self._registry, model):
            return None
        if model in self._released:
            return None
        if not self._released:
            # "Release *a* model" and "release this model" are different actions.
            return (
                f"use case '{self._use_case}' has no model released to it. An administrator of "
                "the use case chooses which approved models it may call; until one is chosen it "
                "can call none."
            )
        return (
            f"'{model}' has not been released to use case '{self._use_case}'. An administrator of "
            f"the use case can add it; it currently has {len(self._released)} model(s)."
        )


class ToolsSupported:
    """The model must be able to answer with a function call (`FRD-131` FR-4).

    A model without tool calling answers a tools request in **prose**, and a client built on
    parsing a function call errors or misreads it.
    """

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        declaration = await self._catalog.declaration(model)
        if declaration.can(Capability.TOOLS):
            return None
        return (
            f"'{model}' does not declare tool calling, so it would answer this request in prose "
            "where a function call was expected."
        )


class SpeechSupported:
    """The model must answer with speech, on a dialect that can ask for it (`FRD-624`).

    A model that cannot would answer in prose, or its provider would refuse without saying why.
    """

    def __init__(self, registry: ProviderRegistry, catalog: ModelCatalog) -> None:
        self._registry = registry
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        declaration = await self._catalog.declaration(model)
        if not declaration.can(Capability.SPEECH):
            missing = "declares no speech output" if declaration.declared else "is undeclared"
            return f"{missing}, so it cannot answer this request with audio"
        provider = await adapter_for(self._registry, self._catalog, model)
        # Undeclared means it cannot: a dialect that never said so has no field for a voice.
        if provider is not None and not getattr(provider, "speaks", False):
            return "the dialect serving this model has no way to ask for speech"
        return None


class MediaTypesSupported:
    """The model must be able to read every attachment the request carries (`ADR-0012` §3).

    Sending the prompt without the document produces no error: it produces a fluent, confident
    answer about a document the model never saw, with a 200. An error is recoverable; that is not.
    """

    def __init__(self, catalog: ModelCatalog, required: frozenset[str]) -> None:
        self._catalog = catalog
        self._required = required

    async def refusal(self, model: str) -> str | None:
        if not self._required:
            return None
        declaration = await self._catalog.declaration(model)
        if not declaration.can(Capability.ATTACHMENTS):
            # Says which: a catalog gap somebody can close, or a fact about the model.
            missing = "declares no attachment support" if declaration.declared else "is undeclared"
            return f"{missing}, so it cannot read the {sorted(self._required)} this request carries"
        unreadable = self._required - declaration.media_types
        if unreadable:
            return f"cannot read {sorted(unreadable)}; it accepts {sorted(declaration.media_types)}"
        return None


class StructuredOutputSupported:
    """The model must be able to constrain its answer to the caller's schema (`FRD-112` §5.3).

    Otherwise a fallback returns prose to a caller about to ``JSON.parse`` it. Which mechanism the
    model uses is not this check's business (`ADR-0011` rule 3).
    """

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        declaration = await self._catalog.declaration(model)
        if declaration.can(Capability.STRUCTURED_OUTPUT):
            return None
        missing = "declares no structured output" if declaration.declared else "is undeclared"
        return f"{missing}, so it cannot return a document matching the schema this request sent"


class OutputCapAccepted:
    """The model must accept the output cap the caller asked for (`FRD-114`).

    The model routing chose is held to it before dispatch, as the caller's `400`
    (`check_declaration`); this asks every other candidate. One that declares a lower maximum is
    refused by its vendor after being called, or answers with less than was asked for.
    """

    def __init__(self, catalog: ModelCatalog, requested: int) -> None:
        self._catalog = catalog
        self._requested = requested

    async def refusal(self, model: str) -> str | None:
        cap = (await self._catalog.declaration(model)).max_output_tokens
        if cap is None or self._requested <= cap:
            return None
        return f"accepts at most {cap} output tokens, and this request asks for {self._requested}"


class ThinkingHonoured:
    """The model must offer the thinking the caller asked for (`FRD-111`).

    A candidate that cannot think as much as was asked answers *less well*, with a 200. A model's
    *default* thinking is not asked for: every candidate is sent its own (`prepare.fitting`).
    """

    def __init__(self, catalog: ModelCatalog, setting: Thinking | None) -> None:
        self._catalog = catalog
        self._setting = setting

    async def refusal(self, model: str) -> str | None:
        if self._setting is None:
            return None
        return permitted_by(self._setting, await self._catalog.declaration(model))


class SamplingExpressible:
    """The candidate's dialect must be able to express every sampling control this request sets.

    A property of the **dialect**, not the model (`ADR-0011`): `seed` on Claude or `top_k` on an
    OpenAI-compatible server produces an answer that differs from a correct one only in the answer.
    """

    def __init__(
        self, registry: ProviderRegistry, requested: frozenset[str], catalog: ModelCatalog
    ) -> None:
        self._registry = registry
        self._requested = requested
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        if not self._requested:
            return None
        provider = await adapter_for(self._registry, self._catalog, model)
        if provider is None:
            return None  # dispatch already reports an unserved model, and says it better
        # Undeclared means unsupported: an adapter without the attribute refuses every control
        # (and a test makes the omission itself fail).
        supported: frozenset[str] = getattr(provider, "sampling_controls", frozenset())
        missing = sorted(self._requested - supported)
        if not missing:
            return None
        return (
            f"the dialect serving this model cannot express {', '.join(missing)}, and a request "
            "answered without them differs only in the answer"
        )


class SamplingAccepted:
    """The model must take the sampling this request sets (`FRD-145`).

    The *model's* half of `SamplingExpressible`: a dialect can have the field and a model still
    refuse every value but one. Anthropic's Claude from Opus 4.7 on answers 400 to `top_k`, to a
    `temperature` other than 1 and to a `top_p` below 0.99, and the catalogue says which models
    those are (`fixed_sampling`). A neutral value asks for nothing and passes — KIRA sends
    `temperature: 1` on every request.
    """

    def __init__(self, catalog: ModelCatalog, request: CanonicalRequest) -> None:
        self._catalog = catalog
        self._set = set_sampling(request)

    async def refusal(self, model: str) -> str | None:
        if not self._set or not (await self._catalog.declaration(model)).fixed_sampling:
            return None
        return (
            f"samples at its own settings and refuses {', '.join(self._set)}; this request sets "
            "them, and an answer without them differs only in the answer"
        )


def set_sampling(request: CanonicalRequest) -> list[str]:
    """The sampling controls ``request`` sets to something other than their neutral value."""
    controls = []
    if request.temperature is not None and request.temperature != NEUTRAL_TEMPERATURE:
        controls.append(f"temperature {request.temperature:g}")
    if request.top_p is not None and request.top_p != NEUTRAL_TOP_P:
        controls.append(f"top_p {request.top_p:g}")
    if request.top_k is not None:
        controls.append(f"top_k {request.top_k}")
    return controls


class SchemaExpressible:
    """The candidate's dialect must be able to express the caller's schema (`ADR-0012` §3).

    Anthropic's schema vocabulary is smaller than the one our surface accepts; a constraint dropped
    silently yields an answer matching the schema the caller *sent*, not the one they *meant*.
    """

    def __init__(
        self, registry: ProviderRegistry, schema: ResponseSchema, catalog: ModelCatalog
    ) -> None:
        self._registry = registry
        self._schema = schema
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        provider = await adapter_for(self._registry, self._catalog, model)
        if provider is None:
            return None  # dispatch already reports an unserved model, and says it better
        # Absent means no restriction this dialect knows of; every restricted dialect declares one.
        check = getattr(provider, "schema_refusal", None)
        if check is None:
            return None
        refusal: str | None = check(self._schema)
        return refusal


def permits(requirements: Sequence[Requirement]) -> Callable[[str], Awaitable[str | None]]:
    """Combine requirements into the predicate the dispatch chain takes.

    First refusal wins: naming one reason is enough to act on.
    """

    async def check(model: str) -> str | None:
        for requirement in requirements:
            refusal = await requirement.refusal(model)
            if refusal is not None:
                return refusal
        return None

    return check
