"""Vertex AI / Model Garden: one transport, two dialects (`FRD-115`, `FRD-119`).

transport          endpoint, region check, credential, errors
auth               the service-account token exchange
regions            where a model runs, and failover across regions (`FRD-609`)
anthropic_mapping  the Anthropic Messages dialect (the Gemini one is `upstreams.gemini_mapping`)
adapters           the two `Upstream` adapters
"""

from __future__ import annotations

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import RegionNotAllowed, check_region, parse_allowed
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid, build_token_source
from aira_gateway.upstreams.vertex.regions import VertexModel
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

    Every failure here is a **startup** failure — unusable credentials, a model in a region this
    deployment does not permit, a malformed model spec: a gateway that starts and quietly serves a
    non-EU region is worse than one that will not start.
    """
    if not settings.vertex_project or not (settings.vertex_credentials or settings.vertex_api_key):
        return []

    specs = [spec.strip() for spec in settings.vertex_models.split(",") if spec.strip()]
    models = [VertexModel.parse(spec) for spec in specs]
    allowed = parse_allowed(settings.allowed_regions)
    for model in models:
        check_region(model.region, allowed)

    # TLS verification stays on (FR-7), stated where it is decided.
    client = httpx.AsyncClient(timeout=settings.vertex_timeout_seconds, verify=True)
    # The service account wins where both are set (FR-3a): silently preferring a key left in the
    # environment would be a downgrade. The JSON is validated here, at startup, and only when set.
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

    # Both dialects, even with nothing configured: the credential decides whether this platform is
    # available, and an adapter with an empty list is what lets the catalogue be the only list.
    google = [model for model in models if model.publisher == "google"]
    anthropic = [model for model in models if model.publisher == "anthropic"]
    upstreams: list[Upstream] = [
        VertexGeminiAdapter(transport, google),
        VertexAnthropicAdapter(
            transport, anthropic, default_max_tokens=settings.vertex_default_max_tokens
        ),
    ]
    return upstreams
