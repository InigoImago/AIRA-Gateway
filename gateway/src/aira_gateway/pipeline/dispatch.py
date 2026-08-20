"""Fallback-aware dispatch, and the conditions a candidate has to meet (FRD-302, ADR-0012 §3).

Tries ``[model, *fallback_models]`` in order and returns the first success, together with the
position of the candidate that answered — the audit trail has to be able to say that a substitution
happened (`FRD-122` FR-3).

**A chain must not be able to degrade a request silently.** That is the rule `ADR-0012` §3 states
for attachments and it applies to every property of a candidate that changes what comes back: a
model that cannot read the PDF, a model that cannot enforce the schema, a model in a region this
request may not use. Falling back to one of those does not produce an error. It produces a fluent,
confident answer — computed from less than the caller sent, or somewhere they did not permit —
returned with a 200 and indistinguishable from a correct one.

So a candidate that fails a condition is **skipped**, the reason is kept, and when no candidate
qualifies the request **fails** with those reasons rather than with "no provider available", which
reads as an upstream outage and sends the reader looking in the wrong place.

Conditions arrive as an async predicate rather than as objects this module knows about: dispatch
should not learn what a media type is when `FRD-110` lands, nor what a region is now.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import AmbiguousModel, ProviderRegistry, UpstreamError

#: Given a model name, why it may not serve this request — or ``None`` if it may.
Permits = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Routing:
    """Everything the catalogue says about **reaching** one candidate.

    Three facts from one declaration, kept together because they are read together and because
    fetching two of them and leaving the third behind is exactly how this went wrong. The chain
    used to ask only for ``(provider, publisher)``; ``addressing`` stayed as the *primary's*, on a
    request that had already been re-pointed at a different model.

    Nowhere is that visible except at the platform that reads it. On Vertex, ``addressing`` is the
    region list, so a fallback catalogued in `europe-west4` was addressed at the primary's
    `europe-west1` — *not deployed here*, then the primary's remaining regions, all equally wrong;
    and a catalogued Vertex fallback behind a primary that carries no addressing at all was refused
    with *"catalogued for this platform and says no region"*, which the catalogue flatly
    contradicts. `ADR-0011`'s rule in its usual clothes: the caller's model name is never the
    platform's addressing, and a chain that changes the first must change the second with it.
    """

    provider: str = ""
    publisher: str = ""
    addressing: dict[str, Any] = field(default_factory=dict)


#: Given a model name, how the catalogue says to reach it.
RoutingOf = Callable[[str], Awaitable[Routing]]


@dataclass(frozen=True, slots=True)
class Skipped:
    """A candidate that was not tried, and why. Kept so the failure can explain itself."""

    model: str
    reason: str


class NoCapableModel(Exception):
    """No candidate could serve the request.

    Distinct from an upstream failure on purpose. "Every model was excluded" is a configuration or
    capability problem the operator can fix; "the upstream is down" is not, and reporting them as
    the same 502 sends whoever reads it to the wrong place.
    """

    def __init__(self, skipped: list[Skipped]) -> None:
        self.skipped = skipped
        detail = "; ".join(f"{entry.model}: {entry.reason}" for entry in skipped)
        super().__init__(f"No model could serve this request ({detail}).")


@dataclass(frozen=True, slots=True)
class Dispatched:
    """A response, which candidate produced it (0 is the primary), and who was passed over."""

    response: CanonicalResponse
    candidate_index: int
    skipped: list[Skipped] = field(default_factory=list)


async def dispatch_with_fallback(
    registry: ProviderRegistry,
    request: CanonicalRequest,
    fallback_models: tuple[str, ...],
    *,
    permits: Permits | None = None,
    routing_of: RoutingOf | None = None,
) -> Dispatched:
    candidates = [request.model, *[m for m in fallback_models if m != request.model]]
    skipped: list[Skipped] = []
    last_error: UpstreamError | None = None

    for index, model in enumerate(candidates):
        # The catalog names who serves a model, so one that was catalogued rather than configured
        # resolves too (`FRD-507`). Asked per candidate, because a fallback chain may cross
        # providers — that is what a chain is for.
        routing = await routing_of(model) if routing_of is not None else Routing()
        provider = registry.provider_for(model, routing.provider, routing.publisher)
        if provider is None:
            # Previously a silent `continue`. A model nobody serves is a configuration mistake,
            # and it should be visible in the failure rather than inferred from its absence.
            skipped.append(Skipped(model, "no provider serves this model"))
            continue
        if permits is not None:
            refusal = await permits(model)
            if refusal is not None:
                skipped.append(Skipped(model, refusal))
                continue
        try:
            # **The addressing moves with the model.** `model_copy` used to change the name alone,
            # which left every hop after the first carrying the primary's platform address — see
            # :class:`Routing`. Only where the chain was told how to look one up: with no
            # ``routing_of`` the request keeps what it arrived with, which is what a caller that
            # resolved the addressing itself expects.
            update: dict[str, Any] = {"model": model}
            if routing_of is not None:
                update["addressing"] = routing.addressing
            response = await provider.generate(request.model_copy(update=update))
        except UpstreamError as exc:
            last_error = exc
        except (AmbiguousModel, RegionNotAllowed) as exc:
            # **A candidate that cannot be addressed is a candidate, not a server error.**
            #
            # Since cataloguing a model became enough to serve it, the address can be wrong in two
            # new ways an operator controls: a platform that needs a region and a catalogue entry
            # that names none, or a region outside `AIRA_ALLOWED_REGIONS`. Both used to escape the
            # chain and reach the caller as a **500** — a configuration fault dressed as our fault,
            # measured on 2026-08-19 for both.
            #
            # Recorded as a skip so a fallback chain moves on, which is what a chain is for, and so
            # the refusal names the model and the reason when nothing else qualifies.
            skipped.append(Skipped(model, str(exc)))
        else:
            return Dispatched(response, index, skipped)

    # An upstream that was *tried* and failed is an outage and is reported as one — the caller may
    # usefully retry. Everything else is a chain that had nothing to offer.
    if last_error is not None:
        raise last_error
    raise NoCapableModel(skipped)
