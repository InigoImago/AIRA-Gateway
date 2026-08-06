"""Microsoft Foundry / Azure OpenAI: the third platform (FRD-120).

    FoundryTransport   resource URL, credential, api-version, Azure error shapes
    └── OpenAIAdapter  unchanged — the dialect was written for this before Azure existed here
        └── AzureRoutes   the deployment in the path, no model in the body

**The diff for this platform does not leave `upstreams/`, and that was the test.** `ADR-0011`
claims transport × dialect × model identity is enough structure for a third vendor; a change that
had reached into the canonical core, the pipeline or a surface would have falsified it. The dialect
gained nothing, the mappers gained nothing, and the routing axis was the one piece genuinely
missing — which is what §5.1 predicted.

Two credentials are supported and the choice is not stylistic. An **API key** is what a developer
has on day one; **Entra** (a bearer token from the shared :class:`TokenSource`) is what an
organisation with a key-rotation policy actually deploys, and building only the first is how a
system ends up with a static secret in production because nothing else was possible.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aira_common.tokens import TokenSource
from aira_gateway.config import GatewaySettings
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.foundry.routes import AzureRoutes, UnknownDeployment
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport

__all__ = [
    "AzureRoutes",
    "FoundryTransport",
    "FoundryDeployment",
    "FoundrySpecInvalid",
    "UnknownDeployment",
    "build_foundry_upstreams",
    "parse_deployments",
]

#: Azure requires an explicit API version on every call, and the default is pinned rather than
#: "latest": a version that moves on its own changes response shapes without a deploy, and the
#: first sign is a mapper reading a field that stopped being sent.
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

    The same shape as `FRD-123`'s server list and for the same reason: this is set in a `.env` and
    a shell, where a quoted JSON blob is a well-known way to lose a character and get an error that
    names a byte offset.
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
            # Two deployments for one caller-facing name is a silent choice of which one served a
            # request — invisible in every log, and the spend attaches to the same model either way.
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


class FoundryTransport(OpenAITransport):
    """Azure's endpoint and credential, over the dialect's own transport contract.

    Subclassed rather than rewritten because everything about *sending* is already right — the
    retries, the streamed-error ordering, the status pass-through that keeps a 429 meaning
    "capacity" rather than being flattened to a 502. What differs is one header.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str = "",
        tokens: TokenSource | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(client=client, timeout=timeout)
        self._azure_key = api_key
        self._tokens = tokens

    async def headers(self) -> dict[str, str]:
        # Azure's key goes in `api-key`, **not** in `Authorization` — sending an Azure key as a
        # bearer token produces a 401 that says nothing about which of the two was wrong.
        if self._azure_key:
            return {"api-key": self._azure_key}
        if self._tokens is not None:
            return {"Authorization": f"Bearer {await self._tokens.token()}"}
        return {}


def build_foundry_upstreams(settings: GatewaySettings) -> list[Upstream]:
    """Build the Foundry adapter from settings, or an empty list when unconfigured.

    Registered only when an endpoint *and* a credential *and* at least one deployment are
    configured. Half a configuration is a gateway that starts and answers 401 for every request,
    which reads as a broken credential rather than as a missing one.
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
            # The same list every transport is measured against (`ADR-0012` §6): Azure's
            # `westeurope` beside Google's `europe-west1`, because "which regions may we use" is
            # one policy question and a per-cloud list would mean a per-cloud audit.
            check_region(entry.region, allowed)

    client = httpx.AsyncClient(base_url=settings.foundry_endpoint.rstrip("/"), verify=True)
    transport = FoundryTransport(
        client=client, api_key=settings.foundry_api_key, timeout=settings.foundry_timeout_seconds
    )
    routes = AzureRoutes(
        {entry.model: entry.deployment for entry in declared}, settings.foundry_api_version
    )

    # **One adapter per region**, not one adapter with a region. Provenance is recorded per model
    # (`FRD-115` FR-10), so a deployment fleet spread across two regions must not be flattened into
    # whichever one happened to be declared first — that would put a residency claim on the audit
    # row that the request did not satisfy, which is worse than recording none.
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
