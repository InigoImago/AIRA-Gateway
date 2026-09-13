"""Fallback-aware dispatch, and the conditions a candidate has to meet (FRD-302, ADR-0012 §3).

Tries ``[model, *fallback_models]`` in order and returns the first success with the position of the
candidate that answered, so the audit trail can say a substitution happened (`FRD-122` FR-3).

**A chain must not degrade a request silently.** A fallback that cannot read the attachment,
enforce the schema, or serve from a permitted region does not fail — it answers fluently from less
than the caller sent, with a 200. So a candidate that fails a condition is **skipped** with its
reason, and when none qualifies the request fails with those reasons (`NoCapableModel`) rather than
as an upstream outage.

Conditions arrive as an async predicate, so dispatch never learns what a region or a media type is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import AmbiguousModel, ProviderRegistry, UpstreamError

#: Given a model name, why it may not serve this request — or ``None`` if it may.
Permits = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Routing:
    """Everything the catalogue says about **reaching** one candidate.

    Kept together because they are read together: a hop that took the fallback's provider but kept
    the primary's ``addressing`` sent it to the primary's regions (`ADR-0011` — the caller's model
    name is never the platform's addressing, and a chain that changes one must change both).
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

    Distinct from an upstream failure: "every model was excluded" is a configuration the operator
    can fix, and reporting it as a 502 sends the reader to the wrong place.
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
        # Asked per candidate: a chain may cross providers, and a catalogued model resolves too
        # (`FRD-507`).
        routing = await routing_of(model) if routing_of is not None else Routing()
        provider = registry.provider_for(model, routing.provider, routing.publisher)
        if provider is None:
            skipped.append(Skipped(model, "no provider serves this model"))
            continue
        if permits is not None:
            refusal = await permits(model)
            if refusal is not None:
                skipped.append(Skipped(model, refusal))
                continue
        try:
            # The addressing moves with the model (see `Routing`). Without ``routing_of`` the
            # request keeps what it arrived with, as a caller that resolved it itself expects.
            update: dict[str, Any] = {"model": model}
            if routing_of is not None:
                update["addressing"] = routing.addressing
            # Per attempt, so each model tried leaves its own model-access record (`FRD-619`).
            with model_call_span(model, purpose="serve"):
                response = await provider.generate(request.model_copy(update=update))
        except UpstreamError as exc:
            last_error = exc
        except (AmbiguousModel, RegionNotAllowed) as exc:
            # A candidate that cannot be addressed — no region catalogued, or one outside
            # `AIRA_ALLOWED_REGIONS` — is a configuration fault, not a 500: skip it and move on.
            skipped.append(Skipped(model, str(exc)))
        else:
            return Dispatched(response, index, skipped)

    # An upstream that was *tried* and failed is an outage, which the caller may usefully retry.
    # Everything else is a chain that had nothing to offer.
    if last_error is not None:
        raise last_error
    raise NoCapableModel(skipped)
