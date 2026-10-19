"""Vertex AI / Model Garden: one transport, three dialects (`FRD-115`, `FRD-119`).

transport          endpoint, region check, credential, errors
auth               the service-account token exchange
regions            where a model runs, and failover across regions (`FRD-609`)
anthropic_mapping  the Anthropic Messages dialect (the Gemini one is `upstreams.gemini_mapping`,
                   the OpenAI one `upstreams.openai.mapping`)
adapters           the `Upstream` adapters: Gemini, Anthropic, and one per open-model publisher
"""

from __future__ import annotations

import httpx

from aira_common.logging import get_logger
from aira_gateway.config import GatewaySettings
from aira_gateway.residency import (
    RegionNotAllowed,
    check_region,
    parse_allowed,
    parse_model_regions,
)
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.exchange import timeout
from aira_gateway.upstreams.vertex.adapters import (
    OPEN_MODEL_PUBLISHERS,
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexOpenModelAdapter,
)
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid
from aira_gateway.upstreams.vertex.billing import BillingPrices
from aira_gateway.upstreams.vertex.credentials import (
    build_identity,
    configured,
    has_identity,
    on_google_cloud,
)
from aira_gateway.upstreams.vertex.regions import VertexModel
from aira_gateway.upstreams.vertex.transport import VertexTransport

__all__ = [
    "CredentialsInvalid",
    "RegionNotAllowed",
    "VertexAnthropicAdapter",
    "VertexGeminiAdapter",
    "VertexModel",
    "VertexOpenModelAdapter",
    "VertexTransport",
    "build_vertex_upstreams",
]


_log = get_logger("aira_gateway.upstreams.vertex")


def build_vertex_upstreams(settings: GatewaySettings) -> list[Upstream]:
    """Build the Vertex adapters from settings, or an empty list when unconfigured.

    Every failure here is a **startup** failure — unusable credentials, a model in a region this
    deployment does not permit, a malformed model spec, a publisher no adapter here speaks: a
    gateway that starts and quietly serves a non-EU region is worse than one that will not start,
    and one that starts without a configured model answers "not found" for it.
    """
    if (
        settings.vertex_project
        and not configured(settings)
        and not settings.vertex_metadata_server
        and on_google_cloud()
    ):
        # Nothing configured, and running on Google Cloud: the machine's own account, as Google's
        # libraries do. Only when **nothing** is set — a configured key that is missing stays an
        # error rather than quietly becoming another identity (`FRD-143`).
        _log.info("vertex_identity_from_metadata_server")
        settings = settings.model_copy(update={"vertex_metadata_server": True})
    has_account = has_identity(settings)
    if not settings.vertex_project or not (has_account or settings.vertex_api_key):
        return []

    specs = [spec.strip() for spec in settings.vertex_models.split(",") if spec.strip()]
    models = [VertexModel.parse(spec) for spec in specs]
    allowed = parse_allowed(settings.allowed_regions)
    by_name = parse_model_regions(settings.allowed_model_regions)
    spoken = {"google", "anthropic", *OPEN_MODEL_PUBLISHERS}
    for model in models:
        check_region(model.region, allowed, model=model.name, for_models=by_name)
        if model.publisher not in spoken:
            raise ValueError(
                f"AIRA_VERTEX_MODELS names '{model.name}' from publisher '{model.publisher}', "
                f"which no Vertex adapter here speaks. Known: {', '.join(sorted(spoken))}."
            )

    # TLS verification stays on (FR-7), stated where it is decided. Connecting is bounded apart
    # from answering, so an unreachable region fails over in seconds (`exchange.timeout`).
    client = httpx.AsyncClient(timeout=timeout(settings.vertex_timeout_seconds), verify=True)
    # The service account wins where both are set (FR-3a): silently preferring a key left in the
    # environment would be a downgrade. The JSON is validated here, at startup, and only when set.
    transport = VertexTransport(
        project=settings.vertex_project,
        tokens=(build_identity(settings, client) if has_account else None),
        api_key="" if has_account else settings.vertex_api_key,
        client=client,
        allowed_regions=allowed,
        model_regions=by_name,
    )

    # Every dialect, even with nothing configured: the credential decides whether this platform is
    # available, and an adapter with an empty list is what lets the catalogue be the only list.
    def published_by(publisher: str) -> list[VertexModel]:
        return [model for model in models if model.publisher == publisher]

    # The billing account's prices (`FRD-509`), asked with the same identity. Not with an API key:
    # a billing account answers only an OAuth identity.
    billing = (
        BillingPrices(
            account=settings.vertex_billing_account,
            tokens=transport.tokens,
            client=client,
            currency=settings.currency,
        )
        if settings.vertex_billing_account and transport.tokens is not None
        else None
    )

    upstreams: list[Upstream] = [
        VertexGeminiAdapter(transport, published_by("google")),
        VertexAnthropicAdapter(
            transport,
            published_by("anthropic"),
            default_max_tokens=settings.vertex_default_max_tokens,
            strict_tools=settings.vertex_partner_strict_tools,
        ),
    ]
    upstreams.extend(
        VertexOpenModelAdapter(
            transport, published_by(publisher), publisher=publisher, thinking_switch=switch
        )
        for publisher, switch in OPEN_MODEL_PUBLISHERS.items()
    )
    for upstream in upstreams:
        upstream.billing = billing  # type: ignore[attr-defined]  # every one is a _VertexAdapter
    return upstreams
