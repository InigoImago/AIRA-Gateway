"""A Vertex token is renewed behind the requests, and an identity service gets seconds, not minutes.

**What this is about.** A Google access token is renewed at 80 % of its lifetime — with an hour's
token, twelve minutes before it expires. The renewal ran **inline, under a lock**: the request
that noticed it was due waited for the identity provider, and every request arriving meanwhile
queued behind it, although the held token was good for twelve more minutes. The calls to the
identity provider went through the model client, whose time limit is the model's — two minutes by
default, raised by operators for long answers — and workload identity federation makes up to
three of them in a row. A slow metadata server, STS or Keycloak froze all Vertex traffic of the
instance for that long, and after a failure began again thirty seconds later.

Driven through the real `VertexGeminiAdapter` → `VertexTransport` → `TokenSource` → acquirer,
sharing one `httpx.AsyncClient`, as `build_vertex_upstreams` builds them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.tokens import TokenSource
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.exchange import timeout
from aira_gateway.upstreams.vertex.adapters import VertexGeminiAdapter
from aira_gateway.upstreams.vertex.auth import IDENTITY_TIMEOUT
from aira_gateway.upstreams.vertex.credentials import build_identity
from aira_gateway.upstreams.vertex.federation import MetadataServerAcquirer
from aira_gateway.upstreams.vertex.regions import VertexModel
from aira_gateway.upstreams.vertex.transport import VertexTransport

MODEL = "gemini-2.5-flash"

#: The model client's allowance, as an installation with long answers sets it.
MODEL_TIMEOUT_SECONDS = 600.0

ANSWER = {
    "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _request() -> CanonicalRequest:
    return CanonicalRequest(model=MODEL, messages=[CanonicalMessage(role=Role.USER, text="hi")])


async def test_a_request_during_a_slow_refresh_is_served_with_the_held_token() -> None:
    """The metadata server answers the first token request and hangs on the renewal. A request
    arriving while the renewal is in flight is served — with the token that is still valid."""
    hang = asyncio.Event()  # never set: the identity provider is slow
    token_requests = 0
    sent_with: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.host == "metadata.google.internal":
            token_requests += 1
            if token_requests > 1:
                await hang.wait()
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        sent_with.append(request.headers["authorization"])
        return httpx.Response(200, json=ANSWER)

    clock = _Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = TokenSource(MetadataServerAcquirer(client), clock=clock)
    transport = VertexTransport(
        project="p", tokens=tokens, client=client, allowed_regions=("europe-west1",)
    )
    adapter = VertexGeminiAdapter(transport, [VertexModel("europe-west1", "google", MODEL)])

    assert (await adapter.generate(_request())).text == "ok"  # the token, fetched at t=1000

    # Fifty minutes on: the renewal is due, and the held token is good for ten more.
    clock.now += 3000
    try:
        answers = [await asyncio.wait_for(adapter.generate(_request()), timeout=2.0)]
        await asyncio.sleep(0.05)  # the renewal has begun, and the metadata server keeps it waiting
        assert token_requests == 2
        answers += [
            await asyncio.wait_for(adapter.generate(_request()), timeout=2.0) for _ in range(3)
        ]
    finally:
        hang.set()
        await asyncio.sleep(0.05)
        await client.aclose()

    assert [answer.text for answer in answers] == ["ok"] * 4
    assert token_requests == 2, "the renewal is one fetch, behind the requests"
    assert sent_with == ["Bearer tok-1"] * 5


# == an identity service's own time limit =========================================================


def _key() -> str:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )


_EXTERNAL_ACCOUNT = {
    "type": "external_account",
    "audience": (
        "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/a/providers/b"
    ),
    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    "token_url": "https://sts.googleapis.com/v1/token",
    "service_account_impersonation_url": (
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        "gw@p.iam.gserviceaccount.com:generateAccessToken"
    ),
}

#: Every identity this gateway can present to Google, configured as an operator would.
IDENTITIES: dict[str, dict[str, Any]] = {
    "service-account": {
        "vertex_credentials": json.dumps({"client_email": "a@b", "private_key": _key()}),
    },
    "metadata-server": {"vertex_metadata_server": True},
    "federation-through-keycloak": {
        "vertex_credentials": json.dumps(_EXTERNAL_ACCOUNT),
        "vertex_federation_token_url": "https://sso.example.com/realms/a/protocol/openid-connect/token",
        "vertex_federation_client_id": "gw",
        "vertex_federation_client_secret": "secret",
    },
    "federation-through-a-token-url": {
        "vertex_credentials": json.dumps(
            {**_EXTERNAL_ACCOUNT, "credential_source": {"url": "http://idp.local/token"}}
        ),
    },
}

#: What each identity service answers.
_ANSWERS = {
    "oauth2.googleapis.com": {"access_token": "t", "expires_in": 3600},
    "metadata.google.internal": {"access_token": "t", "expires_in": 3600},
    "sso.example.com": {"access_token": "kc"},
    "idp.local": {"access_token": "url"},
    "sts.googleapis.com": {"access_token": "federated", "expires_in": 3600},
    "iamcredentials.googleapis.com": {"accessToken": "t", "expireTime": "2099-01-01T00:00:00Z"},
}


@pytest.mark.parametrize("identity", IDENTITIES)
async def test_every_identity_call_has_its_own_short_time_limit(identity: str) -> None:
    """The model client's allowance is minutes, because a model may think that long; an identity
    service that has not answered in seconds is down, and a request with no valid token waits."""
    limits: dict[str, dict[str, float]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        limits[request.url.host] = request.extensions["timeout"]
        if request.url.host in _ANSWERS:
            return httpx.Response(200, json=_ANSWERS[request.url.host])
        return httpx.Response(200, json=ANSWER)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=timeout(MODEL_TIMEOUT_SECONDS)
    )
    settings = GatewaySettings(vertex_project="p", **IDENTITIES[identity])
    transport = VertexTransport(
        project="p",
        tokens=build_identity(settings, client),
        client=client,
        allowed_regions=("europe-west1",),
    )
    await VertexGeminiAdapter(transport, [VertexModel("europe-west1", "google", MODEL)]).generate(
        _request()
    )

    model_call = limits.pop("europe-west1-aiplatform.googleapis.com")
    assert model_call["read"] == MODEL_TIMEOUT_SECONDS
    assert limits, "no identity service was asked"
    for host, limit in limits.items():
        assert limit["read"] == IDENTITY_TIMEOUT.read == 10.0, host
        assert limit["connect"] <= 10.0, host
