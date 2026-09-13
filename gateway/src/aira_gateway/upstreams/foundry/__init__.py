"""Microsoft Foundry / Azure OpenAI: the third platform (`FRD-120`).

    FoundryTransport   resource URL, credential, api-version
    └── OpenAIAdapter  unchanged — the dialect needed nothing for this platform
        └── AzureRoutes   the deployment in the path, no model in the body

The change for this platform did not leave `upstreams/`, which is the test of `ADR-0011`'s claim
that transport × dialect × model identity is enough structure for a new vendor.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.foundry.routes import AzureRoutes, UnknownDeployment
from aira_gateway.upstreams.foundry.transport import FoundryTransport
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter

__all__ = [
    "AzureRoutes",
    "FoundryTransport",
    "FoundryDeployment",
    "FoundrySpecInvalid",
    "UnknownDeployment",
    "build_foundry_upstreams",
    "parse_deployments",
]

#: Azure requires an API version on every call. Pinned, not "latest": a moving version changes
#: response shapes without a deploy. The settings class repeats the value (the adapter must not
#: import the settings default), and `test_foundry.py` holds the two together.
DEFAULT_API_VERSION = "2024-10-21"


class FoundrySpecInvalid(Exception):
    """A deployment declaration that cannot be read. A **startup** failure, like every other."""


@dataclass(frozen=True, slots=True)
class FoundryDeployment:
    """One model, the deployment that serves it, and where that deployment runs."""

    model: str
    deployment: str
    region: str = ""
    embedding: bool = False


def parse_deployments(spec: str) -> list[FoundryDeployment]:
    """Read ``model=deployment[|region][|embed]`` entries, separated by ``;``.

    The same shape as `FRD-123`'s server list, and for the same reason: it is set in a `.env` and a
    shell, where a quoted JSON blob loses characters.
    """
    deployments: list[FoundryDeployment] = []
    seen: set[str] = set()

    for entry in (item.strip() for item in spec.split(";") if item.strip()):
        model, separator, rest = entry.partition("=")
        model = model.strip()
        if not separator or not model or not rest.strip():
            raise FoundrySpecInvalid(
                f"'{entry}' is not a deployment declaration. Expected "
                "'model=deployment[|region][|embed]'."
            )
        if model in seen:
            # Two deployments for one caller-facing name would be a silent choice of which served.
            raise FoundrySpecInvalid(f"Model '{model}' is declared twice.")
        seen.add(model)

        fields = [field.strip() for field in rest.split("|")]
        deployments.append(
            FoundryDeployment(
                model=model,
                deployment=fields[0],
                region=fields[1] if len(fields) > 1 else "",
                embedding=len(fields) > 2 and fields[2].lower() in ("embed", "embedding", "true"),
            )
        )
    return deployments


def build_foundry_upstreams(settings: GatewaySettings) -> list[Upstream]:
    """Build the Foundry adapters from settings, or an empty list when unconfigured.

    An endpoint with deployments but no credential refuses to start: it would answer 401 for every
    request, which reads as a broken credential rather than a missing one.
    """
    if not settings.foundry_endpoint or not settings.foundry_deployments:
        return []
    if not settings.foundry_api_key:
        raise FoundrySpecInvalid(
            "AIRA_FOUNDRY_ENDPOINT is set but no credential is. A gateway that starts without one "
            "answers 401 for every request, which reads as a broken credential rather than a "
            "missing one."
        )

    declared = parse_deployments(settings.foundry_deployments)
    allowed = parse_allowed(settings.allowed_regions)
    for entry in declared:
        if entry.region:
            # The same allow-list every transport is measured against (`ADR-0012` §6).
            check_region(entry.region, allowed)

    client = httpx.AsyncClient(base_url=settings.foundry_endpoint.rstrip("/"), verify=True)
    transport = FoundryTransport(
        client=client, api_key=settings.foundry_api_key, timeout=settings.foundry_timeout_seconds
    )
    routes = AzureRoutes(
        {entry.model: entry.deployment for entry in declared},
        # Empty means unset: Compose passes optional variables as `${VAR:-}`, and Azure refuses
        # `api-version=` with nothing after it.
        settings.foundry_api_version or DEFAULT_API_VERSION,
    )

    # **One adapter per region**: provenance is recorded per model (`FRD-115` FR-10), and a fleet
    # across two regions flattened into one would put a residency claim on the audit row that the
    # request did not satisfy.
    by_region: dict[str, list[FoundryDeployment]] = {}
    for entry in declared:
        by_region.setdefault(entry.region, []).append(entry)

    return [
        OpenAIAdapter(
            transport,
            [entry.model for entry in group if not entry.embedding],
            embedding_models=[entry.model for entry in group if entry.embedding],
            provider="foundry",
            publisher="microsoft",
            region=region,
            routes=routes,
        )
        for region, group in by_region.items()
    ]
