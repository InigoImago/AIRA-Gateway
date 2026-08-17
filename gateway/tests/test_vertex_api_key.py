"""The Agent Platform adapter's second credential: an API key (`FRD-115` FR-3a).

Google renamed Vertex AI to **Gemini Enterprise Agent Platform** and issues API keys to accounts
that never create a service account. Until this existed, an installation holding one could reach
nothing: AIRA's only API-key path was AI Studio, on `generativelanguage.googleapis.com`, which
refuses an Agent Platform key with `API_KEY_SERVICE_BLOCKED` — measured against Google on
2026-08-17, and the reason this file exists.

The property worth defending is not "a header is set". It is that **taking the cheaper credential
does not quietly cost the residency guarantee**: Google's own express mode uses the *global*
endpoint, whose documentation says data is processed anywhere; this adapter keeps its locational
hosts and its per-model region check, which the same key was measured to accept.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aira_common.tokens import TokenUnavailable
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.vertex.transport import VertexTransport

PROJECT = "858738136418"


@pytest.fixture(autouse=True)
def _no_vault_between_cases() -> Any:
    """`VaultSource._cache` is a **class** attribute, so a case that stubs Vault leaves its values
    standing for every case after it.

    Found here rather than reasoned about: the builder case below was handed
    `AIRA_VERTEX_API_KEY=AQ.from-vault` by a Vault case three tests earlier and built an adapter
    where it asserted none. `reset()`'s own docstring says tests must not share this cache; nothing
    was making that true.
    """
    from aira_common.config import VaultSource

    VaultSource.reset()
    yield
    VaultSource.reset()


class _Tokens:
    """A service-account token source. Records whether it was asked at all."""

    def __init__(self, value: str = "exchanged-token", fails: bool = False) -> None:
        self.value = value
        self.fails = fails
        self.asked = 0

    async def token(self) -> str:
        self.asked += 1
        if self.fails:
            raise TokenUnavailable("the assertion was refused")
        return self.value


def _transport(seen: list[httpx.Request], **kwargs: Any) -> VertexTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VertexTransport(project=PROJECT, client=client, **kwargs)


async def test_an_api_key_is_sent_as_the_header_google_expects() -> None:
    seen: list[httpx.Request] = []
    transport = _transport(seen, api_key="AQ.test-key")

    await transport.post(
        transport.url(
            region="europe-west1",
            publisher="google",
            model="gemini-2.5-flash",
            method="generateContent",
        ),
        {"contents": []},
    )

    assert seen[0].headers["x-goog-api-key"] == "AQ.test-key"
    assert "authorization" not in {k.lower() for k in seen[0].headers}


async def test_the_api_key_path_keeps_the_regional_host_and_therefore_the_residency() -> None:
    """The whole argument for putting this on *this* adapter rather than on Google's global one.

    Express mode documents `aiplatform.googleapis.com`, which processes data anywhere; the same key
    answers on `europe-west1-aiplatform.googleapis.com`, which does not. A key-authenticated
    request must go to the second, or `FRD-115` FR-5 becomes a claim nobody can back.
    """
    seen: list[httpx.Request] = []
    transport = _transport(seen, api_key="AQ.test-key")

    url = transport.url(
        region="europe-west1",
        publisher="google",
        model="gemini-2.5-flash",
        method="generateContent",
    )
    await transport.post(url, {"contents": []})

    assert str(seen[0].url).startswith("https://europe-west1-aiplatform.googleapis.com/")
    assert f"/v1/projects/{PROJECT}/locations/europe-west1/" in str(seen[0].url)


async def test_a_region_this_deployment_forbids_is_still_refused_with_a_key() -> None:
    """The check is on the URL builder, so it cannot be bypassed by choosing a credential."""
    transport = _transport([], api_key="AQ.test-key", allowed_regions=("europe-west1",))

    with pytest.raises(Exception) as refused:
        transport.url(
            region="us-central1",
            publisher="google",
            model="gemini-2.5-flash",
            method="generateContent",
        )

    assert "us-central1" in str(refused.value)


async def test_a_service_account_is_still_a_bearer_token() -> None:
    seen: list[httpx.Request] = []
    tokens = _Tokens()
    transport = _transport(seen, tokens=tokens)

    await transport.post(
        transport.url(
            region="europe-west1",
            publisher="google",
            model="gemini-2.5-flash",
            method="generateContent",
        ),
        {"contents": []},
    )

    assert seen[0].headers["authorization"] == "Bearer exchanged-token"
    assert "x-goog-api-key" not in {k.lower() for k in seen[0].headers}
    assert tokens.asked == 1


async def test_an_unavailable_service_account_is_an_upstream_failure_not_a_client_error() -> None:
    """Unchanged by FR-3a, and worth re-asserting beside it: the caller did nothing wrong."""
    transport = _transport([], tokens=_Tokens(fails=True))

    with pytest.raises(UpstreamError) as failed:
        await transport.post(
            transport.url(
                region="europe-west1",
                publisher="google",
                model="gemini-2.5-flash",
                method="generateContent",
            ),
            {"contents": []},
        )

    assert failed.value.status_code == 503


async def test_a_transport_with_no_credential_refuses_to_be_built() -> None:
    """At construction, which is startup. A transport with no credential answers every request with
    the same upstream error and looks exactly like Google being down."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(ValueError) as refused:
        VertexTransport(project=PROJECT, client=client)

    assert "AIRA_VERTEX_API_KEY" in str(refused.value)


# --------------------------------------------------------------------------------------------
# Both credentials must arrive from Vault as readily as from the environment (`FRD-116`).
# The owner asked for exactly that, so it is asserted rather than assumed: `VaultSource` is a
# generic settings source, but "generic" is a claim about code, and a multi-line PEM private key
# passing through a KV store unharmed is a claim about data.
# --------------------------------------------------------------------------------------------

SERVICE_ACCOUNT = (
    '{"client_email":"svc@example.iam.gserviceaccount.com",'
    '"private_key":"-----BEGIN PRIVATE KEY-----\\nline-one\\n'
    'line-two\\n-----END PRIVATE KEY-----\\n",'
    '"token_uri":"https://oauth2.googleapis.com/token"}'
)


def _vault_holding(monkeypatch: pytest.MonkeyPatch, stored: dict[str, str]) -> None:
    """Point the settings at a Vault that holds exactly `stored`, and at nothing else."""
    from aira_common import config as common_config

    monkeypatch.setenv("VAULT_ADDR", "http://vault.test:8200")
    monkeypatch.setenv("VAULT_TOKEN", "root")
    for name in ("AIRA_VERTEX_API_KEY", "AIRA_VERTEX_CREDENTIALS", "AIRA_VERTEX_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(common_config, "load_secrets", lambda: dict(stored))
    common_config.VaultSource.reset()


def test_an_api_key_can_come_from_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    from aira_gateway.config import GatewaySettings

    _vault_holding(monkeypatch, {"AIRA_VERTEX_API_KEY": "AQ.from-vault"})

    assert GatewaySettings().vertex_api_key == "AQ.from-vault"


def test_a_service_account_json_survives_vault_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """The half that could plausibly break: a PEM key is multi-line, and a store that mangled the
    escapes would leave a credential that parses and cannot sign — a failure that shows up as
    Google refusing every request."""
    import json

    from aira_gateway.config import GatewaySettings

    _vault_holding(monkeypatch, {"AIRA_VERTEX_CREDENTIALS": SERVICE_ACCOUNT})

    parsed = json.loads(GatewaySettings().vertex_credentials)
    assert parsed["client_email"] == "svc@example.iam.gserviceaccount.com"
    assert parsed["private_key"].count("\n") == 4


def test_vault_wins_over_the_environment_for_both_credentials_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FRD-116` FR-3: a value present in Vault wins, a value absent from it falls back to the
    environment. Asserted for **both** credentials together, because the failure worth preventing
    is not the precedence itself but a *split* one — a deployment taking the API key from Vault and
    the service account from the environment holds two credentials from two places while believing
    it holds one, and would rotate only half of them.
    """
    from aira_gateway.config import GatewaySettings

    _vault_holding(
        monkeypatch,
        {"AIRA_VERTEX_API_KEY": "AQ.from-vault", "AIRA_VERTEX_CREDENTIALS": SERVICE_ACCOUNT},
    )
    monkeypatch.setenv("AIRA_VERTEX_API_KEY", "AQ.from-env")
    monkeypatch.setenv("AIRA_VERTEX_CREDENTIALS", '{"client_email":"env@example.com"}')

    settings = GatewaySettings()
    assert settings.vertex_api_key == "AQ.from-vault"
    assert "svc@example.iam.gserviceaccount.com" in settings.vertex_credentials


def test_the_environment_is_the_fallback_when_vault_does_not_hold_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same rule, and the one an installation actually starts with."""
    from aira_gateway.config import GatewaySettings

    _vault_holding(monkeypatch, {"AIRA_VERTEX_CREDENTIALS": SERVICE_ACCOUNT})
    monkeypatch.setenv("AIRA_VERTEX_API_KEY", "AQ.from-env")

    assert GatewaySettings().vertex_api_key == "AQ.from-env"


def _settings(**overrides: Any) -> Any:
    from aira_gateway.config import GatewaySettings

    base: dict[str, Any] = {
        "vertex_project": PROJECT,
        "vertex_models": "europe-west1/google/gemini-2.5-flash",
        "allowed_regions": "europe-west1",
    }
    base.update(overrides)
    return GatewaySettings(**base)


def test_the_adapter_is_built_from_an_api_key_alone() -> None:
    """The whole point of FR-3a: an installation with no service account reaches models."""
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    upstreams = build_vertex_upstreams(_settings(vertex_api_key="AQ.only-a-key"))

    assert len(upstreams) == 1


def test_neither_credential_builds_nothing_rather_than_failing() -> None:
    """Unconfigured is not misconfigured — a deployment that uses Ollama only must still start."""
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    assert build_vertex_upstreams(_settings()) == []


def test_a_service_account_is_preferred_where_both_are_configured() -> None:
    """Stated in the code and asserted here, because the failure is silent: a deployment that
    rotated to a service account and left the old key in the environment would keep using the key,
    and the audit trail at Google would go on naming the credential somebody thought was retired.
    """
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    upstreams = build_vertex_upstreams(
        _settings(vertex_api_key="AQ.stale-key", vertex_credentials=SERVICE_ACCOUNT)
    )

    transport = upstreams[0]._transport  # type: ignore[attr-defined]
    assert transport._api_key == ""
    assert transport._tokens is not None
