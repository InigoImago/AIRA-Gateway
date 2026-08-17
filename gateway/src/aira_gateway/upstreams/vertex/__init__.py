"""Vertex AI / Model Garden: one transport, two dialects (FRD-115, FRD-119)."""

from __future__ import annotations

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import RegionNotAllowed, check_region, parse_allowed
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.vertex.adapters import (
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexModel,
)
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid, build_token_source
from aira_gateway.upstreams.vertex.transport import VertexTransport

__all__ = [
    "CredentialsInvalid",
    "RegionNotAllowed",
    "VertexAnthropicAdapter",
    "VertexGeminiAdapter",
    "VertexModel",
    "VertexTransport",
    "build_vertex_upstreams",
]


def build_vertex_upstreams(settings: GatewaySettings) -> list[Upstream]:
    """Build the Vertex adapters from settings, or an empty list when unconfigured.

    Every failure here is a **startup** failure: unusable credentials, a model in a region this
    deployment does not permit, or a malformed model spec. A gateway that starts and then fails
    every request looks like an upstream outage, and a gateway that starts and quietly serves a
    non-EU region is worse than one that will not start at all.
    """
    if not settings.vertex_project or not (settings.vertex_credentials or settings.vertex_api_key):
        return []

    specs = [spec.strip() for spec in settings.vertex_models.split(",") if spec.strip()]
    models = [VertexModel.parse(spec) for spec in specs]
    allowed = parse_allowed(settings.allowed_regions)
    for model in models:
        check_region(model.region, allowed)

    # TLS verification stays on (FR-7). Named here rather than left to the default, because a
    # place where compatibility must not soften a security setting is worth stating where it is
    # decided.
    client = httpx.AsyncClient(timeout=settings.vertex_timeout_seconds, verify=True)
    # **The service account wins where both are set** (`FRD-115` FR-3a): a deployment that has one
    # has made the more deliberate choice, and silently preferring a key left in the environment
    # would be a downgrade nobody asked for. `build_token_source` also *validates* the JSON here,
    # at startup, which is why it is not called at all on the API-key path — there is nothing to
    # validate, and calling it with an empty string would refuse a perfectly configured gateway.
    transport = VertexTransport(
        project=settings.vertex_project,
        tokens=(
            build_token_source(settings.vertex_credentials, client)
            if settings.vertex_credentials
            else None
        ),
        api_key="" if settings.vertex_credentials else settings.vertex_api_key,
        client=client,
        allowed_regions=allowed,
    )

    upstreams: list[Upstream] = []
    google = [model for model in models if model.publisher == "google"]
    anthropic = [model for model in models if model.publisher == "anthropic"]
    if google:
        upstreams.append(VertexGeminiAdapter(transport, google))
    if anthropic:
        upstreams.append(
            VertexAnthropicAdapter(
                transport, anthropic, default_max_tokens=settings.vertex_default_max_tokens
            )
        )
    return upstreams
