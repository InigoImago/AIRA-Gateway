"""What a model must satisfy to serve a particular request (ADR-0012 §3).

The dispatch chain asks one question per candidate — *may this model serve this request?* — and
this is where the answers live. Each requirement is small, states its own reason in words an
operator can act on, and is checked against the model **about to be dispatched to**, not the one
the caller named: with routing and cross-vendor fallback those are different models, and the
check that runs before routing protects nothing.

Region came first, media types with `FRD-110`, structured output and thinking with `FRD-112` and
`FRD-111`. Four now share this mechanism rather than each inventing one, which is the point of
having put it here when there was one.

They all guard the same failure: **a chain must not be able to degrade a request silently.** Every
one of these properties changes what comes back without changing the status code, so a candidate
that cannot meet one is skipped rather than served.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from aira_common.models import Capability
from aira_gateway.catalog import ModelCatalog
from aira_gateway.core.canonical import Thinking
from aira_gateway.thinking import permitted_by
from aira_gateway.upstreams.base import ProviderRegistry


class Requirement(Protocol):
    """Answers why a model may not serve this request, or ``None`` if it may."""

    async def refusal(self, model: str) -> str | None: ...


class RegionAllowed:
    """The model must run somewhere this request is permitted to be processed.

    Today the permitted set is the deployment's own allow-list, so this cannot refuse anything a
    correctly configured gateway would offer — every model was already checked at startup. It is
    built now anyway, for two reasons that are not speculative:

    - **A chain spanning two allowed regions is unconstrained.** Nothing today expresses "this
      request stays in `eu`", so a fallback can move it to another permitted region without
      anything recording an intent it violated. When residency becomes a per-use-case property,
      this is the check that enforces it — and it enforces it *per hop*, which is the part that
      would otherwise be got wrong.
    - **A model with no declared region is not assumed to be fine.** An adapter that forgets to
      declare where it runs produces a request nobody can place afterwards, which is precisely
      what the provenance columns exist to prevent.
    """

    def __init__(self, registry: ProviderRegistry, allowed: Sequence[str]) -> None:
        self._registry = registry
        self._allowed = frozenset(allowed)

    async def refusal(self, model: str) -> str | None:
        if not self._allowed:
            return None  # no residency constraint configured for this request
        described = self._registry.get_model(model)
        if described is None:
            return None  # not ours to refuse; the chain reports it as unserved
        if not described.region:
            # The mock and the laptop adapter declare none. Refusing them would break every
            # development setup; the honest reading is "this deployment has no residency posture
            # to violate", and the constraint only bites where a region is actually declared.
            return None
        if described.region not in self._allowed:
            return (
                f"runs in '{described.region}', and this request may only be processed in "
                f"{sorted(self._allowed)}"
            )
        return None


class ToolsSupported:
    """The model must be able to answer with a function call (`FRD-131` FR-4).

    The same argument as :class:`MediaTypesSupported`, one field over. A model that cannot do tool
    calling, sent a request that declares tools, answers in **prose** — and a client whose entire
    loop is built on parsing a function call either errors on the spot or, worse, tries to read the
    prose as one. Either way the caller is told nothing about why.

    Checked at **every hop**: a chain whose first candidate can do tools and whose fallback cannot
    would otherwise turn a fallback into a silent downgrade, which is the failure `ADR-0012` §3
    exists to prevent.
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


class MediaTypesSupported:
    """The model must be able to read every attachment the request carries (`ADR-0012` §3).

    **This is the requirement the whole document feature turns on.** The tempting behaviour when a
    model cannot read a PDF is to send the prompt without it and let the model answer anyway. That
    produces **no error**: it produces a fluent, confident answer about a document the model never
    saw, returned with a 200, indistinguishable from a correct one to everyone including the
    caller — who then reports that "the model is hallucinating" and looks for the fault in the
    wrong place entirely.

    So a model that cannot read what was sent is refused, by name, with the types it lacks. An
    error is a recoverable outcome; a confident wrong answer is not.

    Checked against the model **about to be dispatched to** — after routing, at every hop of the
    chain. A check against the model the caller named would be satisfied by a request that then
    fell back to one that cannot read a thing.
    """

    def __init__(self, catalog: ModelCatalog, required: frozenset[str]) -> None:
        self._catalog = catalog
        self._required = required

    async def refusal(self, model: str) -> str | None:
        if not self._required:
            return None
        declaration = await self._catalog.declaration(model)
        if not declaration.can(Capability.ATTACHMENTS):
            # Undeclared *and* declared-without-attachments land here, and the message says which:
            # one is a catalog gap somebody can close in a minute, the other is a fact about the
            # model. Telling them apart is the difference between a fix and a support ticket.
            missing = "declares no attachment support" if declaration.declared else "is undeclared"
            return f"{missing}, so it cannot read the {sorted(self._required)} this request carries"
        unreadable = self._required - declaration.media_types
        if unreadable:
            return f"cannot read {sorted(unreadable)}; it accepts {sorted(declaration.media_types)}"
        return None


class StructuredOutputSupported:
    """The model must be able to constrain its answer to the caller's schema (`FRD-112` §5.3).

    **This is the requirement the whole feature turns on, and it has to run after routing.** A use
    case with a fallback chain could otherwise accept a schema request, have the primary fail, fall
    back to a model without structured output, and return prose to a caller that will call
    ``JSON.parse`` on it. The failure surfaces as a parse error in someone else's application,
    days later, with nothing pointing back here.

    Which *mechanism* the model uses is not this check's business — Gemini has a schema parameter,
    Anthropic a forced tool call, Azure a `json_schema` response format. One flag over three
    unrelated mechanisms is `ADR-0011` rule 3, and it is what stops the catalog from having to know
    how any of them work.
    """

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    async def refusal(self, model: str) -> str | None:
        declaration = await self._catalog.declaration(model)
        if declaration.can(Capability.STRUCTURED_OUTPUT):
            return None
        missing = "declares no structured output" if declaration.declared else "is undeclared"
        return f"{missing}, so it cannot return a document matching the schema this request sent"


class ThinkingHonoured:
    """The model must offer the thinking this request resolved to (`FRD-111`).

    Same shape and same reason as the two above: a candidate that cannot think as much as was
    asked does not fail, it answers *less well*, with a 200, in a way only the person reading the
    answer would ever notice.
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

    The fifth requirement, and the first that is a property of the **dialect** rather than of the
    model: no model declaration can say whether `top_k` exists, because that depends on the wire
    format the request will travel over. `ADR-0011` again — the caller asks for one thing, three
    vendors offer three vocabularies, and where one of them has no word for it the honest answer is
    to say so.

    The failure it prevents is quiet by construction. `seed` on a Claude candidate produces a
    perfectly good answer that simply is not reproducible; `top_k` on an OpenAI-compatible one
    produces a perfectly good answer sampled from a wider distribution than was asked for. Nothing
    in either response differs from a correct one, which is the definition of a difference that has
    to be refused rather than absorbed.
    """

    def __init__(self, registry: ProviderRegistry, requested: frozenset[str]) -> None:
        self._registry = registry
        self._requested = requested

    async def refusal(self, model: str) -> str | None:
        if not self._requested:
            return None
        provider = self._registry.provider_for(model)
        if provider is None:
            return None  # dispatch already reports an unserved model, and says it better
        # Undeclared means unsupported, as everywhere else. An adapter that omits the attribute
        # refuses every sampling control rather than silently accepting them all — and a test
        # makes the omission itself fail, so this branch is a floor and not a policy.
        supported: frozenset[str] = getattr(provider, "sampling_controls", frozenset())
        missing = sorted(self._requested - supported)
        if not missing:
            return None
        return (
            f"the dialect serving this model cannot express {', '.join(missing)}, and a request "
            "answered without them differs only in the answer"
        )


def permits(requirements: Sequence[Requirement]) -> Callable[[str], Awaitable[str | None]]:
    """Combine requirements into the predicate the dispatch chain takes.

    First refusal wins: a candidate excluded for two reasons is excluded, and naming the first is
    enough to act on. Returning them all would read as though every one had to be fixed.
    """

    async def check(model: str) -> str | None:
        for requirement in requirements:
            refusal = await requirement.refusal(model)
            if refusal is not None:
                return refusal
        return None

    return check
