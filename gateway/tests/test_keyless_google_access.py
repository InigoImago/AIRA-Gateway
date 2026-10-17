"""Reaching Google with no Google key (`FRD-142`).

Hermetic: one `httpx.MockTransport` plays Keycloak, Google's STS, the IAM credentials API, the
metadata server and Vertex, routed by host, and records what each was sent. What these tests hold
is the exchange itself — which identity goes where, in which form — and that a configuration which
would send the installation's identity anywhere but Google is refused before it can.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.diagnosis import from_body
from aira_gateway.upstreams.vertex import build_vertex_upstreams
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid
from aira_gateway.upstreams.vertex.credentials import build_identity
from aira_gateway.upstreams.vertex.federation import (
    ACCESS_TOKEN_TYPE,
    JWT_TOKEN_TYPE,
    TOKEN_EXCHANGE_GRANT,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

AUDIENCE = (
    "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/aira/providers/kc"
)
IMPERSONATE = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
    "aira-gateway@p.iam.gserviceaccount.com:generateAccessToken"
)
KEYCLOAK = "https://sso.example.com/realms/aira/protocol/openid-connect/token"
SECRET = "the-clients-secret"


def _config(**over: Any) -> dict[str, Any]:
    return {
        "type": "external_account",
        "audience": AUDIENCE,
        "subject_token_type": JWT_TOKEN_TYPE,
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": IMPERSONATE,
        **over,
    }


class Google:
    """Every party, by host; each answer replaceable to play a refusal."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.answers: dict[str, httpx.Response] = {
            "sso.example.com": httpx.Response(200, json={"access_token": "kc-token"}),
            "sts.googleapis.com": httpx.Response(
                200, json={"access_token": "federated", "expires_in": 3600}
            ),
            "iamcredentials.googleapis.com": httpx.Response(
                200, json={"accessToken": "impersonated", "expireTime": "2099-01-01T00:00:00Z"}
            ),
            "metadata.google.internal": httpx.Response(
                200, json={"access_token": "from-the-machine", "expires_in": 3599}
            ),
            "europe-west1-aiplatform.googleapis.com": httpx.Response(200, json={}),
            "idp.local": httpx.Response(200, json={"token": "url-token"}),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self.answers[request.url.host]

    def to(self, host: str) -> list[httpx.Request]:
        return [request for request in self.sent if request.url.host == host]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def _settings(**over: Any) -> GatewaySettings:
    return GatewaySettings(**{"vertex_project": "p", **over})


def _federated(**over: Any) -> GatewaySettings:
    return _settings(
        **{
            "vertex_credentials": json.dumps(_config()),
            "vertex_federation_token_url": KEYCLOAK,
            "vertex_federation_client_id": "aira-gateway",
            "vertex_federation_client_secret": SECRET,
            **over,
        }
    )


async def _call_vertex(google: Google, settings: GatewaySettings) -> httpx.Request:
    client = google.client()
    transport = VertexTransport(project="p", tokens=build_identity(settings, client), client=client)
    await transport.post(
        "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1/x", {}
    )
    return google.to("europe-west1-aiplatform.googleapis.com")[-1]


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


# ---- FR-1: Keycloak's token, exchanged, acting as the service account ----------------------------


async def test_a_keycloak_token_becomes_the_service_accounts_for_an_hour() -> None:
    google = Google()

    vertex = await _call_vertex(google, _federated())

    (keycloak,) = google.to("sso.example.com")
    assert _form(keycloak) == {"grant_type": "client_credentials"}
    assert keycloak.headers["authorization"].startswith("Basic "), "the client authenticates"

    (sts,) = google.to("sts.googleapis.com")
    assert _form(sts) == {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "audience": AUDIENCE,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "requested_token_type": ACCESS_TOKEN_TYPE,
        "subject_token_type": JWT_TOKEN_TYPE,
        "subject_token": "kc-token",
    }

    (iam,) = google.to("iamcredentials.googleapis.com")
    assert str(iam.url) == IMPERSONATE
    assert iam.headers["authorization"] == "Bearer federated"
    assert json.loads(iam.content) == {
        "scope": ["https://www.googleapis.com/auth/cloud-platform"],
        "lifetime": "3600s",
    }

    assert vertex.headers["authorization"] == "Bearer impersonated"
    assert all(SECRET not in str(request.url) for request in google.sent)


async def test_without_impersonation_the_federated_token_is_used_directly() -> None:
    google = Google()
    config = _config()
    del config["service_account_impersonation_url"]

    vertex = await _call_vertex(google, _federated(vertex_credentials=json.dumps(config)))

    assert google.to("iamcredentials.googleapis.com") == []
    assert vertex.headers["authorization"] == "Bearer federated"


async def test_the_token_lifetime_asked_for_is_the_configurations() -> None:
    google = Google()
    config = _config(service_account_impersonation={"token_lifetime_seconds": 1800})

    await _call_vertex(google, _federated(vertex_credentials=json.dumps(config)))

    (iam,) = google.to("iamcredentials.googleapis.com")
    assert json.loads(iam.content)["lifetime"] == "1800s"


# ---- FR-2: the configuration's own sources -------------------------------------------------------


async def test_a_token_from_a_file_is_read_at_every_exchange(tmp_path: Any) -> None:
    token = tmp_path / "token"
    token.write_text("first\n")
    config = _config(credential_source={"file": str(token)})
    google = Google()
    client = google.client()
    tokens = build_identity(_settings(vertex_credentials=json.dumps(config)), client)

    await tokens._acquirer.acquire(0.0)  # type: ignore[attr-defined]
    token.write_text("second")
    await tokens._acquirer.acquire(1.0)  # type: ignore[attr-defined]

    assert [_form(r)["subject_token"] for r in google.to("sts.googleapis.com")] == [
        "first",
        "second",
    ]


async def test_a_token_from_a_json_file_is_read_from_its_field(tmp_path: Any) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"id_token": "in-a-field"}))
    config = _config(
        credential_source={
            "file": str(token),
            "format": {"type": "json", "subject_token_field_name": "id_token"},
        }
    )
    google = Google()

    await _call_vertex(google, _settings(vertex_credentials=json.dumps(config)))

    assert _form(google.to("sts.googleapis.com")[0])["subject_token"] == "in-a-field"


async def test_a_token_from_a_local_url_is_asked_with_its_headers() -> None:
    config = _config(
        credential_source={
            "url": "http://idp.local/token",
            "headers": {"Metadata": "True"},
            "format": {"type": "json", "subject_token_field_name": "token"},
        }
    )
    google = Google()

    await _call_vertex(google, _settings(vertex_credentials=json.dumps(config)))

    (asked,) = google.to("idp.local")
    assert asked.headers["metadata"] == "True"
    assert _form(google.to("sts.googleapis.com")[0])["subject_token"] == "url-token"


async def test_the_configuration_can_come_from_a_file(tmp_path: Any) -> None:
    path = tmp_path / "federation.json"
    path.write_text(json.dumps(_config()))
    google = Google()

    vertex = await _call_vertex(
        google,
        _settings(
            vertex_credentials_file=str(path),
            vertex_federation_token_url=KEYCLOAK,
            vertex_federation_client_id="aira-gateway",
            vertex_federation_client_secret=SECRET,
        ),
    )

    assert vertex.headers["authorization"] == "Bearer impersonated"


# ---- FR-3: the metadata server -------------------------------------------------------------------


async def test_on_google_cloud_the_machine_is_the_identity() -> None:
    google = Google()

    vertex = await _call_vertex(google, _settings(vertex_metadata_server=True))

    (asked,) = google.to("metadata.google.internal")
    assert asked.headers["metadata-flavor"] == "Google"
    assert vertex.headers["authorization"] == "Bearer from-the-machine"


def test_each_keyless_identity_registers_the_platform() -> None:
    assert build_vertex_upstreams(_settings(vertex_metadata_server=True))
    assert build_vertex_upstreams(_federated())


# ---- FR-4: refused at start ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "said"),
    [
        (_config(audience="projects/123/pools/aira"), "audience"),
        (
            _config(
                token_url="https://sts.attacker.example/v1/token", credential_source={"file": "/x"}
            ),
            "token_url",
        ),
        (
            _config(
                token_url="http://sts.googleapis.com/v1/token", credential_source={"file": "/x"}
            ),
            "token_url",
        ),
        (
            _config(
                service_account_impersonation_url="https://elsewhere.example/x",
                credential_source={"file": "/x"},
            ),
            "service_account_impersonation_url",
        ),
        (_config(credential_source={"executable": {"command": "/bin/x"}}), "executable"),
        (_config(credential_source={"environment_id": "aws1"}), "AWS"),
        (_config(), "nowhere to get the identity"),
        (
            _config(
                credential_source={"file": "/x"},
                service_account_impersonation={"token_lifetime_seconds": 60},
            ),
            "600 and 43200",
        ),
    ],
)
def test_a_configuration_that_would_fail_or_leak_stops_the_start(
    config: dict[str, Any], said: str
) -> None:
    with pytest.raises(CredentialsInvalid, match=said):
        build_vertex_upstreams(_settings(vertex_credentials=json.dumps(config)))


def test_federation_without_its_client_stops_the_start() -> None:
    with pytest.raises(CredentialsInvalid, match="CLIENT_ID and _CLIENT_SECRET"):
        build_vertex_upstreams(
            _settings(
                vertex_credentials=json.dumps(_config()), vertex_federation_token_url=KEYCLOAK
            )
        )


def test_federation_with_a_key_instead_of_a_configuration_stops_the_start() -> None:
    with pytest.raises(CredentialsInvalid, match="external_account configuration"):
        build_vertex_upstreams(
            _settings(
                vertex_credentials=json.dumps({"type": "service_account"}),
                vertex_federation_token_url=KEYCLOAK,
                vertex_federation_client_id="c",
                vertex_federation_client_secret="s",
            )
        )


def test_a_credential_of_another_type_stops_the_start() -> None:
    with pytest.raises(CredentialsInvalid, match="authorized_user"):
        build_vertex_upstreams(
            _settings(vertex_credentials=json.dumps({"type": "authorized_user"}))
        )


def test_the_metadata_server_and_credentials_together_stop_the_start() -> None:
    with pytest.raises(ValueError, match="METADATA_SERVER"):
        _settings(vertex_metadata_server=True, vertex_credentials="{}")
    with pytest.raises(CredentialsInvalid, match="nothing to federate"):
        build_vertex_upstreams(
            _settings(vertex_metadata_server=True, vertex_federation_token_url=KEYCLOAK)
        )


# ---- FR-5: what a refusal says -------------------------------------------------------------------


async def _refused_with(host: str, response: httpx.Response) -> UpstreamError:
    google = Google()
    google.answers[host] = response
    with pytest.raises(UpstreamError) as caught:
        await _call_vertex(google, _federated())
    return caught.value


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        (
            "The audience in ID Token [account] does not match the expected audience.",
            "AUDIENCE_MISMATCH",
        ),
        (
            "The given credential is rejected by the attribute condition.",
            "ATTRIBUTE_CONDITION_REJECTED",
        ),
        ("Error connecting to the given credential's issuer.", "ISSUER_UNREACHABLE"),
    ],
)
async def test_a_refused_exchange_says_which_setting_to_change(
    description: str, reason: str
) -> None:
    error = await _refused_with(
        "sts.googleapis.com",
        httpx.Response(400, json={"error": "invalid_grant", "error_description": description}),
    )

    assert error.status_code == 503
    assert error.diagnosis is not None
    assert error.diagnosis.reason == reason
    assert description not in error.message


async def test_a_federated_identity_that_may_not_act_as_the_account_is_told_the_role() -> None:
    error = await _refused_with(
        "iamcredentials.googleapis.com",
        httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "message": "Permission denied",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "IAM_PERMISSION_DENIED",
                            "metadata": {"permission": "iam.serviceAccounts.getAccessToken"},
                        }
                    ],
                }
            },
        ),
    )

    assert error.diagnosis is not None
    assert "roles/iam.workloadIdentityUser" in error.diagnosis.explanation


async def test_keycloak_refusing_the_client_is_named_and_the_secret_is_not() -> None:
    error = await _refused_with(
        "sso.example.com",
        httpx.Response(
            401,
            json={
                "error": "unauthorized_client",
                "error_description": "Invalid client or Invalid client credentials",
            },
        ),
    )

    assert error.diagnosis is not None
    assert error.diagnosis.reason == "CLIENT_REJECTED"
    assert SECRET not in error.message and SECRET not in str(error.diagnosis)


def test_a_client_without_service_accounts_is_told_where_to_turn_them_on() -> None:
    found = from_body(
        {
            "error": "unauthorized_client",
            "error_description": "Client not enabled to retrieve service account",
        },
        400,
    )

    assert found is not None
    assert found.reason == "SERVICE_ACCOUNTS_DISABLED"


# ---- every way an exchange can fail is a named refusal, never a crash ---------------------------


def _boom(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom", request=request)


@pytest.mark.parametrize(
    ("host", "answer"),
    [
        ("sso.example.com", httpx.Response(200, json={"no": "token"})),
        ("sso.example.com", httpx.Response(200, text="not json")),
        ("sts.googleapis.com", httpx.Response(200, json={})),
        ("iamcredentials.googleapis.com", httpx.Response(200, json={})),
    ],
)
async def test_an_answer_without_a_token_is_a_refusal(host: str, answer: httpx.Response) -> None:
    error = await _refused_with(host, answer)

    assert error.status_code == 503


@pytest.mark.parametrize("host", ["sso.example.com", "sts.googleapis.com", "idp.local"])
async def test_a_party_that_cannot_be_reached_is_a_refusal_with_its_cause(host: str) -> None:
    google = Google()
    original = google.__call__

    def routed(request: httpx.Request) -> httpx.Response:
        return _boom(request) if request.url.host == host else original(request)

    config = _config(credential_source={"url": "http://idp.local/t"})
    settings = (
        _settings(vertex_credentials=json.dumps(config)) if host == "idp.local" else _federated()
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(routed))
    transport = VertexTransport(project="p", tokens=build_identity(settings, client), client=client)

    with pytest.raises(UpstreamError) as caught:
        await transport.post("https://europe-west1-aiplatform.googleapis.com/x", {})

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == "CONNECTION_FAILED"


async def test_a_url_source_that_refuses_is_a_refusal() -> None:
    google = Google()
    google.answers["idp.local"] = httpx.Response(403, json={})
    config = _config(credential_source={"url": "http://idp.local/t"})

    with pytest.raises(UpstreamError):
        await _call_vertex(google, _settings(vertex_credentials=json.dumps(config)))


@pytest.mark.parametrize(
    ("content", "fmt"),
    [
        ("", {}),
        ("{}", {"type": "json", "subject_token_field_name": "id_token"}),
        ("not json", {"type": "json", "subject_token_field_name": "id_token"}),
        ('{"id_token": "x"}', {"type": "json"}),
    ],
)
async def test_a_file_without_a_token_is_a_refusal(
    tmp_path: Any, content: str, fmt: dict[str, Any]
) -> None:
    token = tmp_path / "token"
    token.write_text(content)
    config = _config(credential_source={"file": str(token), "format": fmt})

    with pytest.raises(UpstreamError):
        await _call_vertex(Google(), _settings(vertex_credentials=json.dumps(config)))


async def test_a_token_file_that_is_gone_is_a_refusal(tmp_path: Any) -> None:
    config = _config(credential_source={"file": str(tmp_path / "gone")})

    with pytest.raises(UpstreamError):
        await _call_vertex(Google(), _settings(vertex_credentials=json.dumps(config)))


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, json={}),
        httpx.Response(200, json={}),
    ],
)
async def test_a_metadata_server_without_a_token_is_a_refusal(answer: httpx.Response) -> None:
    google = Google()
    google.answers["metadata.google.internal"] = answer

    with pytest.raises(UpstreamError):
        await _call_vertex(google, _settings(vertex_metadata_server=True))


async def test_no_metadata_server_says_this_is_not_google_cloud() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_boom))
    transport = VertexTransport(
        project="p",
        tokens=build_identity(_settings(vertex_metadata_server=True), client),
        client=client,
    )

    with pytest.raises(UpstreamError) as caught:
        await transport.post("https://europe-west1-aiplatform.googleapis.com/x", {})

    assert caught.value.diagnosis is not None


async def test_an_unreadable_expiry_is_taken_as_googles_hour() -> None:
    google = Google()
    google.answers["iamcredentials.googleapis.com"] = httpx.Response(
        200, json={"accessToken": "impersonated", "expireTime": "whenever"}
    )
    client = google.client()
    tokens = build_identity(_federated(), client)

    token = await tokens._acquirer.acquire(0.0)  # type: ignore[attr-defined]

    assert token.expires_at == 3600.0


@pytest.mark.parametrize(
    "config",
    [
        _config(credential_source="a string"),
        {**_config(credential_source={"file": "/x"}), "token_url": 7},
    ],
)
def test_a_malformed_configuration_stops_the_start(config: dict[str, Any]) -> None:
    with pytest.raises(CredentialsInvalid):
        build_vertex_upstreams(_settings(vertex_credentials=json.dumps(config)))


def test_a_configuration_file_that_cannot_be_read_stops_the_start(tmp_path: Any) -> None:
    with pytest.raises(CredentialsInvalid, match="cannot be read"):
        build_vertex_upstreams(_settings(vertex_credentials_file=str(tmp_path / "gone.json")))


def test_a_key_that_is_not_json_is_still_named_as_a_key() -> None:
    with pytest.raises(CredentialsInvalid, match="not valid JSON"):
        build_vertex_upstreams(_settings(vertex_credentials="not json"))
