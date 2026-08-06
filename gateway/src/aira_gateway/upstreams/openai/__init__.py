"""The OpenAI wire dialect, and the local endpoint that made it worth building (FRD-123).

Azure OpenAI speaks it (`FRD-120`), Model Garden's self-deploy side serves it (`ADR-0012`), and
Ollama exposes it — so the dialect built here for a local model is the one Foundry will reuse. The
same work counted once instead of twice.
"""

from __future__ import annotations

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.mapping import DialectUnsupported
from aira_gateway.upstreams.openai.transport import OpenAITransport

__all__ = [
    "DialectUnsupported",
    "OpenAIAdapter",
    "OpenAITransport",
    "build_local_upstream",
]


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_local_upstream(settings: GatewaySettings) -> list[Upstream]:
    """Build the local (Ollama) adapter from settings, or an empty list when unconfigured.

    Registered **only** when a URL is set, exactly like the Vertex and Generative Language
    adapters. A verification tool that appears in a deployment nobody asked for it in is a
    verification tool that eventually serves production traffic (`FRD-123` §2).

    The declared region is **recorded, not checked**, and that is a deliberate asymmetry with the
    Vertex transport. `AIRA_ALLOWED_REGIONS` is a list of *cloud* regions — Google's `europe-west1`
    beside Azure's `westeurope` — and it exists because a preview model in the wrong region is a
    residency violation nobody notices. A self-hosted endpoint is the opposite case: the operator
    knows exactly where it runs and names it (`on-premises`, a data-centre code), and refusing to
    start because that name is not in a cloud's vocabulary would be the check misfiring on the one
    deployment with the strongest residency story. What matters is that the audit row says where
    the request went, and it does.
    """
    if not settings.ollama_url:
        return []

    models = _split(settings.ollama_models)
    embedding_models = _split(settings.ollama_embedding_models)
    if not models and not embedding_models:
        return []

    client = httpx.AsyncClient(base_url=settings.ollama_url, verify=True)
    transport = OpenAITransport(client=client, timeout=settings.ollama_timeout_seconds)
    return [
        OpenAIAdapter(
            transport,
            models,
            embedding_models=embedding_models,
            provider="ollama",
            publisher="local",
            region=settings.ollama_region.strip(),
        )
    ]
