"""The OpenAI wire dialect, and the servers reached through it (`FRD-123`).

Azure OpenAI (`FRD-120`), Model Garden's self-deployed models (`ADR-0012`) and Ollama all speak it;
what differs between them is the transport.

A self-hosted server is a system in its own right: a deployment can attach several, each
configured, priced and **audited** separately. That is why the configuration is a list of named
servers rather than one URL — with a single endpoint, "which machine answered" has no answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import DialectUnsupported, Upstream
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport

__all__ = [
    "DialectUnsupported",
    "OpenAIAdapter",
    "OpenAIServer",
    "OpenAITransport",
    "ServerSpecInvalid",
    "build_openai_upstreams",
    "parse_servers",
]


class ServerSpecInvalid(Exception):
    """A server declaration that cannot be read.

    A **startup** failure: a gateway that starts with some servers silently dropped answers "model
    not found" for them, which sends whoever debugs it to the catalog.
    """


@dataclass(frozen=True, slots=True)
class OpenAIServer:
    """One machine speaking the OpenAI dialect, and everything that distinguishes it.

    ``name`` reaches the audit row as the provider, so a fleet is separable in a report. There is
    deliberately no credential field (`FRD-123` §8).
    """

    name: str
    url: str
    models: tuple[str, ...] = ()
    embedding_models: tuple[str, ...] = ()
    region: str = ""
    timeout: float = 300.0

    @property
    def serves_anything(self) -> bool:
        return bool(self.models or self.embedding_models)


def _split(value: str, separator: str = ",") -> list[str]:
    return [item.strip() for item in value.split(separator) if item.strip()]


def parse_servers(spec: str, *, default_timeout: float = 300.0) -> list[OpenAIServer]:
    """Read ``name=url|models|embeddings|region`` entries, one per server, separated by `;`.

    Not JSON: this is set in a `.env` file and a shell, where a quoted JSON blob loses characters
    and fails with a byte offset. A bad entry's message names the entry.

    ::

        AIRA_OPENAI_SERVERS="gpu-a=http://gpu-a:11434|qwen3:8b|nomic-embed-text|dc-frankfurt;
                             gpu-b=http://gpu-b:11434|llama3.1:70b||dc-berlin"
    """
    servers: list[OpenAIServer] = []
    seen: set[str] = set()

    for entry in _split(spec, ";"):
        name, separator, rest = entry.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ServerSpecInvalid(
                f"'{entry}' is not a server declaration. Expected "
                "'name=url|models|embedding_models|region'."
            )
        if name in seen:
            # Two servers under one name would share their audit rows, wrong in a way nothing shows.
            raise ServerSpecInvalid(f"Two servers are declared as '{name}'.")
        seen.add(name)

        fields = rest.split("|")
        url = fields[0].strip()
        if not url.startswith(("http://", "https://")):
            raise ServerSpecInvalid(f"Server '{name}' has no usable URL: '{url}'.")

        server = OpenAIServer(
            name=name,
            url=url,
            models=tuple(_split(fields[1]) if len(fields) > 1 else []),
            embedding_models=tuple(_split(fields[2]) if len(fields) > 2 else []),
            region=fields[3].strip() if len(fields) > 3 else "",
            timeout=default_timeout,
        )
        if not server.serves_anything:
            raise ServerSpecInvalid(
                f"Server '{name}' declares no models. A server nobody can address is a "
                "configuration mistake, not an empty set."
            )
        servers.append(server)
    return servers


def _legacy_server(settings: GatewaySettings) -> list[OpenAIServer]:
    """The single-endpoint settings, read as a server named ``ollama`` — exactly equivalent to one
    entry in `AIRA_OPENAI_SERVERS`, for the common one-machine setup."""
    if not settings.ollama_url:
        return []
    server = OpenAIServer(
        name="ollama",
        url=settings.ollama_url,
        models=tuple(_split(settings.ollama_models)),
        embedding_models=tuple(_split(settings.ollama_embedding_models)),
        region=settings.ollama_region.strip(),
        timeout=settings.ollama_timeout_seconds,
    )
    return [server] if server.serves_anything else []


def build_openai_upstreams(settings: GatewaySettings) -> list[Upstream]:
    """One adapter per declared server, or an empty list when none are configured.

    A declared region is enforced exactly like a cloud one, against the same allow-list and at
    startup (`ADR-0012` §6): a gateway that starts and then refuses everything looks like an
    upstream outage. A server declares **no** region unless the operator names one, so a laptop
    keeps working; naming one opts in to the evidence on the audit row and to the check.
    """
    servers = parse_servers(
        settings.openai_servers, default_timeout=settings.ollama_timeout_seconds
    )
    servers.extend(_legacy_server(settings))
    if not servers:
        return []

    allowed = parse_allowed(settings.allowed_regions)
    upstreams: list[Upstream] = []
    for server in servers:
        if server.region:
            check_region(server.region, allowed)
        client = httpx.AsyncClient(base_url=server.url, verify=True)
        transport = OpenAITransport(client=client, timeout=server.timeout)
        upstreams.append(
            OpenAIAdapter(
                transport,
                list(server.models),
                embedding_models=list(server.embedding_models),
                provider=server.name,
                publisher="local",
                region=server.region,
            )
        )
    return upstreams
