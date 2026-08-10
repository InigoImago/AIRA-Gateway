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

from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError

#: Given a model name, why it may not serve this request — or ``None`` if it may.
Permits = Callable[[str], Awaitable[str | None]]


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
    provider_of: Callable[[str], Awaitable[str]] | None = None,
) -> Dispatched:
    candidates = [request.model, *[m for m in fallback_models if m != request.model]]
    skipped: list[Skipped] = []
    last_error: UpstreamError | None = None

    for index, model in enumerate(candidates):
        # The catalog names who serves a model, so one that was catalogued rather than configured
        # resolves too (`FRD-507`). Asked per candidate, because a fallback chain may cross
        # providers — that is what a chain is for.
        declared = await provider_of(model) if provider_of is not None else ""
        provider = registry.provider_for(model, declared)
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
            response = await provider.generate(request.model_copy(update={"model": model}))
        except UpstreamError as exc:
            last_error = exc
        else:
            return Dispatched(response, index, skipped)

    # An upstream that was *tried* and failed is an outage and is reported as one — the caller may
    # usefully retry. Everything else is a chain that had nothing to offer.
    if last_error is not None:
        raise last_error
    raise NoCapableModel(skipped)
