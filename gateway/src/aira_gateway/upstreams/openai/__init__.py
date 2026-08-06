"""The OpenAI wire dialect, and the servers reached through it (FRD-123).

Azure OpenAI speaks it (`FRD-120`), Model Garden's self-deploy side serves it (`ADR-0012`), and
Ollama exposes it — so one dialect reaches all three. What differs between them is the transport:
where the endpoint is, what credential it takes, where it runs.

**An Ollama server is a system in its own right, not a test fixture.** A deployment can attach
several — a GPU box in one data centre, a second beside it, a workstation for a team that needs a
model nobody else does — and each is configured, addressed, priced and *audited* separately. That
is why the configuration is a list of named servers rather than one URL: with a single endpoint
setting, "which machine answered this request" has no answer, and for a self-hosted fleet that is
exactly the question an audit exists to answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import Upstream
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.mapping import DialectUnsupported
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

    A **startup** failure, like every other configuration error in this layer. A gateway that
    starts with half its servers silently dropped answers "model not found" for the rest, which
    reads as a catalog problem and sends whoever debugs it to the wrong place.
    """


@dataclass(frozen=True, slots=True)
class OpenAIServer:
    """One machine speaking the OpenAI dialect, and everything that distinguishes it.

    ``name`` is not decoration. It reaches the audit row as the provider, so a fleet of local
    servers is separable in a report — "which box served this, and how much did that box cost us"
    is unanswerable when every one of them logs as `ollama`.
    """

    name: str
    url: str
    models: tuple[str, ...] = ()
    embedding_models: tuple[str, ...] = ()
    region: str = ""
    api_key: str = ""
    timeout: float = 300.0

    @property
    def serves_anything(self) -> bool:
        return bool(self.models or self.embedding_models)


def _split(value: str, separator: str = ",") -> list[str]:
    return [item.strip() for item in value.split(separator) if item.strip()]


def parse_servers(spec: str, *, default_timeout: float = 300.0) -> list[OpenAIServer]:
    """Read ``name=url|models|embeddings|region`` entries, one per server, separated by `;`.

    Chosen over JSON because this is set in a `.env` file and a shell, where a quoted JSON blob is
    a well-known way to lose a character and get an error that names a byte offset. The separators
    are positional and the message on a bad entry names the entry.

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
            # Two servers under one name would each overwrite the other's audit rows, and the
            # figures would be wrong in a way nothing reports.
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
    """The single-endpoint settings, read as a server named ``ollama``.

    Kept because a one-machine setup is the common one and making it write a list would be
    ceremony. It is exactly equivalent to one entry in `AIRA_OPENAI_SERVERS`.
    """
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

    Registered **only** when something is configured, exactly like the Vertex and Generative
    Language adapters — a system that appears in a deployment nobody asked for it in eventually
    serves production traffic.

    **A declared region is enforced exactly like a cloud one**, and the correction is worth
    recording because the first draft of this function claimed the opposite. It said the region
    was "recorded, not checked" — and then the first real request to a local server came back
    *"runs in 'on-premises', and this request may only be processed in [...]"*, because
    `RegionAllowed` quite correctly checks every model that declares one. The comment described an
    intention; the system had a rule. The rule was right.

    So there is no asymmetry, only a default: a server declares **no** region unless the operator
    names one. No claim, nothing to enforce, and a laptop keeps working. Naming one is opting in to
    the evidence — the audit row then says where the request went — and opting in to the check,
    which happens **here, at startup**, rather than as a 400 on every request. A gateway that
    starts and then refuses everything looks like an upstream outage; one that will not start names
    the setting to fix.
    """
    servers = parse_servers(
        settings.openai_servers, default_timeout=settings.ollama_timeout_seconds
    )
    servers.extend(_legacy_server(settings))
    if not servers:
        return []

    # Same rule and the same list as every other transport (`ADR-0012` §6): "which regions may we
    # use" is one policy question, and a per-cloud list would mean a per-cloud audit. A locally
    # named region has to be in it too — that is what makes it a claim somebody permitted rather
    # than a label somebody typed.
    allowed = parse_allowed(settings.allowed_regions)

    upstreams: list[Upstream] = []
    for server in servers:
        if server.region:
            check_region(server.region, allowed)
        client = httpx.AsyncClient(base_url=server.url, verify=True)
        transport = OpenAITransport(client=client, api_key=server.api_key, timeout=server.timeout)
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


#: The old name, kept so a single-endpoint setup reads the same. It always built a list.
build_local_upstream = build_openai_upstreams
