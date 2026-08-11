"""Microsoft Foundry / Azure OpenAI — the third platform (FRD-120).

Hermetic: an ``httpx.MockTransport`` stands in for Azure, so the URL, the credential header, the
deployment indirection and the region handling are all exercised without a subscription. That is
the same split every other adapter uses, and here it is the only option — there is no Azure to
point at.

**What is really under test is `ADR-0011`.** It claims transport × dialect × model identity is
enough structure for a third vendor. The dialect gained nothing for this platform, the mappers
gained nothing, and the only genuinely missing piece was the routing axis — so the claim survives.
The architecture assertion in ``test_vertex.py`` checks the other half: that nothing above
``upstreams/`` learned the word "Azure".
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    Role,
)
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.foundry import (
    FoundrySpecInvalid,
    FoundryTransport,
    build_foundry_upstreams,
    parse_deployments,
)
from aira_gateway.upstreams.foundry.routes import AzureRoutes, UnknownDeployment
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter

Handler = Callable[[httpx.Request], httpx.Response]

ENDPOINT = "https://contoso.openai.azure.com"
API_VERSION = "2024-10-21"


def _adapter(handler: Handler, deployments: dict[str, str] | None = None) -> OpenAIAdapter:
    client = httpx.AsyncClient(base_url=ENDPOINT, transport=httpx.MockTransport(handler))
    transport = FoundryTransport(client=client, api_key="azure-secret")
    routes = AzureRoutes(deployments or {"gpt-4o": "prod-gpt4o"}, API_VERSION)
    return OpenAIAdapter(
        transport,
        ["gpt-4o"],
        provider="foundry",
        publisher="microsoft",
        region="westeurope",
        routes=routes,
    )


def _request(model: str = "gpt-4o") -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text="hello")])


def _ok(payload: dict) -> Handler:
    return lambda request: httpx.Response(200, json=payload)


COMPLETION = {
    "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
}


# == addressing: the deployment is not the model =================================================


async def test_the_deployment_goes_in_the_path_and_the_model_does_not_go_in_the_body() -> None:
    """`FRD-120` §5.2. If a deployment name were allowed to *be* the model name, every use case's
    pipeline config would embed Azure resource naming — and pricing would break quietly, because
    `FRD-403` prices by model and a deployment called `production` has no price. Unpriced traffic
    is counted apart rather than as zero, so the spend figure would simply stop being complete."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=COMPLETION)

    await _adapter(handler).generate(_request())

    assert seen["path"] == "/openai/deployments/prod-gpt4o/chat/completions"
    assert seen["query"] == {"api-version": API_VERSION}
    # The path already named the deployment. A body `model` would put a *caller-facing* name on
    # the wire where a reader would take it for the deployment — two strings that look like one.
    assert "model" not in seen["body"]


async def test_the_response_is_attributed_to_the_model_the_caller_named() -> None:
    """The whole point of the indirection: what comes back is `gpt-4o`, not `prod-gpt4o`, so the
    price, the report and the audit all key on something that has a price."""
    response = await _adapter(_ok(COMPLETION)).generate(_request())
    assert response.model == "gpt-4o"


async def test_an_embedding_uses_the_deployment_path_too() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})

    adapter = _adapter(handler, {"text-embedding-3-small": "prod-embed"})
    request = CanonicalEmbeddingRequest(model="text-embedding-3-small", texts=["hi"])
    assert await adapter.embed(request) == [[0.5]]
    assert seen["path"] == "/openai/deployments/prod-embed/embeddings"
    assert "model" not in seen["body"]


async def test_a_model_with_no_deployment_says_so_rather_than_reaching_a_404() -> None:
    """Azure answers 404 for a deployment that does not exist, which reads as "the model is gone"
    instead of "nobody told us where it lives" — and sends whoever debugs it to the wrong system."""
    adapter = _adapter(_ok(COMPLETION), {"gpt-4o": "prod-gpt4o"})
    with pytest.raises(UnknownDeployment, match="mystery"):
        await adapter.generate(_request("mystery"))


def test_a_deployment_name_with_awkward_characters_is_encoded() -> None:
    """The name is chosen by whoever created the resource, and Azure permits characters that would
    otherwise change the path."""
    routes = AzureRoutes({"m": "weird/name?x"}, API_VERSION)
    assert "weird%2Fname%3Fx" in routes.chat("m")


# == the credential ==============================================================================


async def test_the_key_goes_in_the_azure_header_not_in_authorization() -> None:
    """Sending an Azure key as a bearer token produces a 401 that says nothing about which of the
    two was wrong — and the two are configured in different places."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=COMPLETION)

    await _adapter(handler).generate(_request())

    assert seen["api-key"] == "azure-secret"
    assert "bearer" not in seen.get("authorization", "").lower()


async def test_an_entra_token_is_fetched_per_request_rather_than_captured_once() -> None:
    """A token expires. Reading it once at construction is the version that works for an hour and
    then fails for as long as the process lives — the failure a shared `TokenSource` exists to
    prevent, and it only shows up in a long-running deployment."""

    class _Rotating:
        def __init__(self) -> None:
            self.issued = 0

        async def token(self) -> str:
            self.issued += 1
            return f"entra-{self.issued}"

    tokens = _Rotating()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=COMPLETION)

    client = httpx.AsyncClient(base_url=ENDPOINT, transport=httpx.MockTransport(handler))
    transport = FoundryTransport(client=client, tokens=tokens)  # type: ignore[arg-type]
    adapter = OpenAIAdapter(transport, ["gpt-4o"], routes=AzureRoutes({"gpt-4o": "d"}, API_VERSION))

    await adapter.generate(_request())
    await adapter.generate(_request())

    assert seen == ["Bearer entra-1", "Bearer entra-2"]


# == configuration, which fails at startup or not at all =========================================


def test_a_deployment_declaration_is_read_field_by_field() -> None:
    parsed = parse_deployments(
        "gpt-4o=prod-gpt4o|westeurope;text-embedding-3-small=prod-embed|westeurope|embed"
    )
    assert [entry.model for entry in parsed] == ["gpt-4o", "text-embedding-3-small"]
    assert parsed[0].deployment == "prod-gpt4o"
    assert parsed[0].region == "westeurope"
    assert parsed[0].embedding is False
    assert parsed[1].embedding is True


@pytest.mark.parametrize(
    "spec",
    ["no-equals", "=prod-gpt4o", "gpt-4o=", "gpt-4o=a;gpt-4o=b"],
)
def test_an_unreadable_declaration_refuses_to_start(spec: str) -> None:
    """Two deployments for one caller-facing name is the one worth naming: it would be a silent
    choice of which served a request, invisible in every log, with the spend attaching to the same
    model either way."""
    with pytest.raises(FoundrySpecInvalid):
        parse_deployments(spec)


def test_nothing_configured_registers_nothing() -> None:
    assert build_foundry_upstreams(GatewaySettings()) == []


def test_an_endpoint_without_a_credential_refuses_to_start() -> None:
    """Half a configuration is a gateway that starts and answers 401 for every request, which
    reads as a broken credential rather than as a missing one."""
    settings = GatewaySettings(foundry_endpoint=ENDPOINT, foundry_deployments="gpt-4o=prod-gpt4o")
    with pytest.raises(FoundrySpecInvalid, match="credential"):
        build_foundry_upstreams(settings)


def test_a_region_outside_the_allow_list_refuses_to_start() -> None:
    """One list for every cloud (`ADR-0012` §6): Azure's `westeurope` beside Google's
    `europe-west1`, because "which regions may we use" is one policy question and a per-cloud list
    would mean a per-cloud audit."""
    settings = GatewaySettings(
        foundry_endpoint=ENDPOINT,
        foundry_api_key="k",
        foundry_deployments="gpt-4o=prod|eastus",
        allowed_regions="westeurope,europe-west1",
    )
    with pytest.raises(RegionNotAllowed, match="eastus"):
        build_foundry_upstreams(settings)


def test_deployments_in_two_regions_become_two_adapters() -> None:
    """Provenance is recorded per model (`FRD-115` FR-10). Flattening a fleet into whichever region
    was declared first would put a residency claim on the audit row that the request did not
    satisfy — worse than recording none at all."""
    settings = GatewaySettings(
        foundry_endpoint=ENDPOINT,
        foundry_api_key="k",
        foundry_deployments="a=dep-a|westeurope;b=dep-b|swedencentral",
        allowed_regions="westeurope,swedencentral",
    )
    described = {m.name: m for u in build_foundry_upstreams(settings) for m in u.models()}

    assert described["a"].region == "westeurope"
    assert described["b"].region == "swedencentral"
    assert described["a"].provider == "foundry"
    assert described["a"].publisher == "microsoft"


def test_a_configured_platform_registers_its_models_without_ambiguity() -> None:
    settings = GatewaySettings(
        foundry_endpoint=ENDPOINT,
        foundry_api_key="k",
        foundry_deployments="gpt-4o=prod-gpt4o|westeurope;emb=prod-emb|westeurope|embed",
        allowed_regions="westeurope",
    )
    upstreams = build_foundry_upstreams(settings)
    registry = ProviderRegistry(list(upstreams))

    assert registry.provider_for("gpt-4o") is not None
    described = {m.name: m for m in registry.models()}
    assert "generateContent" in described["gpt-4o"].supported_methods
    assert "embedContent" in described["emb"].supported_methods
    assert "generateContent" not in described["emb"].supported_methods


# == the plain form is unchanged =================================================================


async def test_a_non_azure_endpoint_still_names_the_model_in_the_body() -> None:
    """The routing axis must not have changed the platform that had none. `StandardRoutes` is the
    default, so an Ollama or direct-OpenAI endpoint keeps posting to one path with the model in
    the body — which is what makes this an addition rather than a migration."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=COMPLETION)

    from aira_gateway.upstreams.openai.transport import OpenAITransport

    client = httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(handler))
    adapter = OpenAIAdapter(OpenAITransport(client=client), ["gpt-4o"])
    await adapter.generate(_request())

    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "gpt-4o"


def test_azure_owns_no_provider_name_and_offers_no_importable_listing() -> None:
    """`FRD-507` stage C, and this platform is what the distinction was written for.

    Cataloguing a model is enough to serve it exactly where the model name is the **whole**
    addressing (stage B). Here it is not: `/openai/models` answers "which models could this
    resource run", and each of them needs a deployment created first. An adapter that claimed its
    provider name would let a catalogued Azure model resolve to it and fail on a deployment nobody
    created — a 404 that reads as "the model is gone" while the catalog says it is ready. An
    import offered from that listing would produce the same entry, with the console vouching for
    it.

    Foundry builds the **OpenAI adapter class**, so both answers have to be properties of the
    routing axis rather than of the class, or the plain endpoint loses them too.
    """
    azure = _adapter(_ok(COMPLETION))

    assert azure.serves_provider == ""
    assert azure.enumerates is False
