"""What a model must satisfy to serve a particular request (ADR-0012 §3).

The dispatch chain asks one question per candidate — *may this model serve this request?* — and
this is where the answers live. Each requirement is small, states its own reason in words an
operator can act on, and is checked against the model **about to be dispatched to**, not the one
the caller named: with routing and cross-vendor fallback those are different models, and the
check that runs before routing protects nothing.

Region is the first requirement. Media types follow with `FRD-110`, structured output with
`FRD-112`. They share this mechanism rather than each inventing one, which is the point of putting
it here before there are three of them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

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
