"""Fallback-aware dispatch for non-streaming generation (FRD-302).

Tries ``[model, *fallback_models]`` in order, skipping models no provider serves, and returns
the first successful response. If every candidate fails, the last upstream error propagates.
"""

from __future__ import annotations

from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError


async def dispatch_with_fallback(
    registry: ProviderRegistry, request: CanonicalRequest, fallback_models: tuple[str, ...]
) -> CanonicalResponse:
    candidates = [request.model, *[m for m in fallback_models if m != request.model]]
    last_error: UpstreamError | None = None
    for model in candidates:
        provider = registry.provider_for(model)
        if provider is None:
            continue
        try:
            return await provider.generate(request.model_copy(update={"model": model}))
        except UpstreamError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise UpstreamError(f"No provider available for model '{request.model}'.")
