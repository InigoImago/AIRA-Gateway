"""Fallback-aware dispatch for non-streaming generation (FRD-302).

Tries ``[model, *fallback_models]`` in order, skipping models no provider serves, and returns
the first successful response. If every candidate fails, the last upstream error propagates.

The *position* of the candidate that answered is returned alongside the response, because the
audit trail has to be able to say that a substitution happened (FRD-122 FR-3). Deriving it later
from "the response model differs from the requested one" would be one inference too many: with
cross-vendor chains (ADR-0012) that difference is exactly the fact somebody will need to explain a
shift in spend, and an inferred fact is one that stops being true when an adapter changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError


@dataclass(frozen=True, slots=True)
class Dispatched:
    """A response, and which candidate of the chain produced it (0 is the primary)."""

    response: CanonicalResponse
    candidate_index: int


async def dispatch_with_fallback(
    registry: ProviderRegistry, request: CanonicalRequest, fallback_models: tuple[str, ...]
) -> Dispatched:
    candidates = [request.model, *[m for m in fallback_models if m != request.model]]
    last_error: UpstreamError | None = None
    for index, model in enumerate(candidates):
        provider = registry.provider_for(model)
        if provider is None:
            continue
        try:
            response = await provider.generate(request.model_copy(update={"model": model}))
        except UpstreamError as exc:
            last_error = exc
        else:
            return Dispatched(response, index)
    if last_error is not None:
        raise last_error
    raise UpstreamError(f"No provider available for model '{request.model}'.")
