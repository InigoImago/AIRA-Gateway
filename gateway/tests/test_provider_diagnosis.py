"""Why a provider refused, or could not be reached (`FRD-141`).

The status alone sends whoever sets up a model to guess between an API that is not enabled, a
missing role, a model not offered in the region and a proxy that inspects TLS. So the cause a
provider states in structured form is read, explained in our words, and shown to whoever checks the
model — and the provider's free text, which can carry a URL with a key in it, is not.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.tokens import StaticTokenSource, TokenSource
from aira_gateway.api.incidents.model_check import _reach
from aira_gateway.config import GatewaySettings
from aira_gateway.diagnostics import describe_failure
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.diagnosis import Diagnosis, from_body, from_transport
from aira_gateway.upstreams.exchange import opened_stream
from aira_gateway.upstreams.vertex import build_vertex_upstreams
from aira_gateway.upstreams.vertex.auth import (
    CredentialsInvalid,
    GoogleServiceAccountAcquirer,
    KeyFile,
    ServiceAccount,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

#: Stands in for whatever a provider's prose might quote. It must never reach any message.
LEAK = "AIzaSy-the-installations-key"

URL = "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1/x"


def _google(status: int, google_status: str, *info: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {
            "code": status,
            "message": f"Something about ?key={LEAK} the provider said in prose.",
            "status": google_status,
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", **entry} for entry in info
            ],
        }
    }


def _transport(handler: Any, tokens: TokenSource | None = None) -> VertexTransport:
    return VertexTransport(
        project="p",
        tokens=tokens or StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def _refusal(status: int, body: Any) -> UpstreamError:
    transport = _transport(lambda request: httpx.Response(status, json=body))
    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})
    return caught.value


# ---- FR-1: Google's reason word, explained -------------------------------------------------------


async def test_an_api_that_is_not_enabled_says_which_and_where() -> None:
    error = await _refusal(
        403,
        _google(
            403,
            "PERMISSION_DENIED",
            {
                "reason": "SERVICE_DISABLED",
                "domain": "googleapis.com",
                "metadata": {"service": "aiplatform.googleapis.com", "consumer": "projects/4711"},
            },
        ),
    )

    assert error.status_code == 403, "a status is never changed"
    assert error.message == "Google Vertex AI returned 403. Reason: SERVICE_DISABLED."
    assert error.diagnosis is not None
    assert "aiplatform.googleapis.com" in error.diagnosis.explanation
    assert "project 4711" in error.diagnosis.explanation
    # The caller is told which fault it is and not where the installation's project lives.
    assert "4711" not in error.message


async def test_a_missing_role_names_the_permission_and_the_role_to_grant() -> None:
    error = await _refusal(
        403,
        _google(
            403,
            "PERMISSION_DENIED",
            {
                "reason": "IAM_PERMISSION_DENIED",
                "metadata": {"permission": "aiplatform.endpoints.predict"},
            },
        ),
    )

    assert error.diagnosis is not None
    assert error.diagnosis.reason == "IAM_PERMISSION_DENIED"
    assert "aiplatform.endpoints.predict" in error.diagnosis.explanation
    assert "roles/aiplatform.user" in error.diagnosis.explanation


async def test_a_model_that_is_not_there_points_at_region_and_model_garden() -> None:
    error = await _refusal(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": LEAK}})

    assert error.diagnosis is not None
    assert error.diagnosis.reason == "NOT_FOUND"
    assert "region" in error.diagnosis.explanation
    assert "Model Garden" in error.diagnosis.explanation


async def test_the_providers_prose_is_never_repeated() -> None:
    for status in (401, 403, 404, 429, 500):
        error = await _refusal(
            status, _google(status, "PERMISSION_DENIED", {"reason": "SERVICE_DISABLED"})
        )
        assert LEAK not in error.message
        assert LEAK not in str(error.diagnosis)


def test_a_metadata_value_that_is_not_an_identifier_is_not_quoted() -> None:
    found = from_body(
        _google(
            403,
            "PERMISSION_DENIED",
            {
                "reason": "SERVICE_DISABLED",
                "metadata": {"service": f"https://x/?key={LEAK}", "consumer": "projects/1"},
            },
        ),
        403,
    )

    assert found is not None
    assert LEAK not in found.explanation
    assert "the API" in found.explanation


def test_a_reason_word_that_is_not_a_word_is_not_repeated() -> None:
    found = from_body(_google(403, "", {"reason": f"key={LEAK}"}), 403)

    assert found == Diagnosis(
        "PERMISSION_DENIED",
        "The provider accepted the credential and refused it this call: a missing role or a "
        "restricted key.",
    )


async def test_a_refused_stream_carries_its_cause_too() -> None:
    body = _google(403, "PERMISSION_DENIED", {"reason": "BILLING_DISABLED"})
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, json=body))
    )

    with pytest.raises(UpstreamError) as caught:
        async with opened_stream(client, URL, "Vertex upstream", json={}):
            pass

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == "BILLING_DISABLED"


def test_another_dialects_code_is_repeated_as_it_spells_it() -> None:
    found = from_body({"error": {"code": "DeploymentNotFound", "message": LEAK}}, 404)

    assert found is not None
    assert found.reason == "DeploymentNotFound"
    assert LEAK not in found.explanation


def test_an_answer_without_a_body_is_explained_by_its_status() -> None:
    assert from_body("not an object", 401) == Diagnosis(
        "UNAUTHENTICATED", "The provider did not accept the credential."
    )
    assert from_body({}, 500) is None, "a provider's internal error has no cause we can state"


# ---- FR-2: the connection's own cause ------------------------------------------------------------


def _failing(exc: Exception) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _raised_from(outer: httpx.HTTPError, inner: BaseException) -> httpx.HTTPError:
    outer.__cause__ = inner
    return outer


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            _raised_from(
                httpx.ConnectError("boom"),
                ssl.SSLCertVerificationError("certificate verify failed"),
            ),
            "CERTIFICATE_NOT_TRUSTED",
        ),
        (_raised_from(httpx.ConnectError("boom"), socket.gaierror(-2, "x")), "NAME_NOT_RESOLVED"),
        (httpx.ProxyError("boom"), "PROXY_REFUSED"),
        (httpx.ConnectTimeout("boom"), "CONNECT_TIMEOUT"),
        (httpx.ConnectError("boom"), "CONNECTION_FAILED"),
        (httpx.ReadTimeout("boom"), "READ_TIMEOUT"),
    ],
)
async def test_a_connection_that_failed_says_why(error: httpx.HTTPError, reason: str) -> None:
    transport = _transport(_failing(error))

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == reason
    assert caught.value.message.endswith(f"Reason: {reason}.")


async def test_an_untrusted_certificate_names_the_host_and_the_setting() -> None:
    error = _raised_from(
        httpx.ConnectError("boom"), ssl.SSLCertVerificationError("certificate verify failed")
    )
    transport = _transport(_failing(error))

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.diagnosis is not None
    assert "europe-west1-aiplatform.googleapis.com" in caught.value.diagnosis.explanation
    assert "SSL_CERT_FILE" in caught.value.diagnosis.explanation


def test_an_unrelated_failure_states_no_cause() -> None:
    assert from_transport(RuntimeError("no")) is None


# ---- FR-3: the token exchange --------------------------------------------------------------------

_PRIVATE_KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode("ascii")
)


def _token_endpoint(status: int, body: dict[str, Any]) -> TokenSource:
    account = ServiceAccount.from_json(
        json.dumps({"client_email": "a@b", "private_key": _PRIVATE_KEY})
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    return TokenSource(GoogleServiceAccountAcquirer(account, client))


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        ("Invalid JWT Signature.", "KEY_NOT_ACCEPTED"),
        ("Invalid grant: account not found", "ACCOUNT_NOT_FOUND"),
        (
            "Invalid JWT: Token must be a short-lived token (60 minutes) and in a reasonable "
            "timeframe. Check your iat and exp values in the JWT claim.",
            "CLOCK_SKEW",
        ),
    ],
)
async def test_a_refused_token_exchange_says_why(description: str, reason: str) -> None:
    tokens = _token_endpoint(400, {"error": "invalid_grant", "error_description": description})
    transport = _transport(lambda request: httpx.Response(200, json={}), tokens=tokens)

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.status_code == 503, "the caller did nothing wrong"
    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == reason
    assert description not in str(caught.value.diagnosis), "matched, never repeated"


async def test_an_unknown_grant_error_is_named_and_not_quoted() -> None:
    tokens = _token_endpoint(401, {"error": "unauthorized_client", "error_description": LEAK})
    transport = _transport(lambda request: httpx.Response(200, json={}), tokens=tokens)

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == "UNAUTHORIZED_CLIENT"
    assert LEAK not in caught.value.message
    assert LEAK not in str(caught.value.diagnosis)


def test_a_grant_error_that_is_not_a_word_is_not_repeated() -> None:
    assert from_body({"error": f"key={LEAK}"}, 400) is None


async def test_a_token_endpoint_behind_an_inspecting_proxy_says_so() -> None:
    account = ServiceAccount.from_json(
        json.dumps({"client_email": "a@b", "private_key": _PRIVATE_KEY})
    )
    error = _raised_from(
        httpx.ConnectError("boom"), ssl.SSLCertVerificationError("certificate verify failed")
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(_failing(error)))
    tokens = TokenSource(GoogleServiceAccountAcquirer(account, client))
    transport = _transport(lambda request: httpx.Response(200, json={}), tokens=tokens)

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.diagnosis is not None
    assert caught.value.diagnosis.reason == "CERTIFICATE_NOT_TRUSTED"


# ---- FR-4: where it is shown ---------------------------------------------------------------------


async def test_the_model_check_leads_with_the_cause() -> None:
    async def ping(model: str, addressing: dict[str, Any]) -> str:
        raise UpstreamError(
            "Vertex upstream returned 403. Reason: SERVICE_DISABLED.",
            403,
            Diagnosis("SERVICE_DISABLED", "Enable the API."),
        )

    verdict = await _reach(ping, "gemini-x", "europe-west1")

    assert verdict == {
        "reachable": False,
        "detail": "Not reachable (403 SERVICE_DISABLED: Enable the API.).",
        "reason": "SERVICE_DISABLED",
        "explanation": "Enable the API.",
    }


def test_a_failure_without_a_cause_is_still_its_type_alone() -> None:
    assert describe_failure(RuntimeError(f"url?key={LEAK}")) == "RuntimeError"


# ---- FR-5: the key as a file, rotated without a restart ------------------------------------------


def _key_json(email: str) -> str:
    return json.dumps({"client_email": email, "private_key": _PRIVATE_KEY})


def _recording_token_endpoint(issuers: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assertion = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))[
            "assertion"
        ]
        claims = pyjwt.decode(assertion, options={"verify_signature": False})
        issuers.append(claims["iss"])
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_key_file_is_read_again_when_it_changes(tmp_path: Any) -> None:
    path = tmp_path / "key.json"
    path.write_text(_key_json("old@p"))
    issuers: list[str] = []
    acquirer = GoogleServiceAccountAcquirer(KeyFile(str(path)), _recording_token_endpoint(issuers))

    await acquirer.acquire(0.0)
    path.write_text(_key_json("rotated@p"))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    await acquirer.acquire(1.0)

    assert issuers == ["old@p", "rotated@p"]


async def test_a_key_file_broken_by_a_rotation_keeps_the_held_token(tmp_path: Any) -> None:
    path = tmp_path / "key.json"
    path.write_text(_key_json("old@p"))
    clock = [0.0]
    tokens = TokenSource(
        GoogleServiceAccountAcquirer(KeyFile(str(path)), _recording_token_endpoint([])),
        clock=lambda: clock[0],
    )
    assert await tokens.token() == "t"

    path.write_text("{ half a key")
    clock[0] = 3500.0  # due for a refresh, still valid

    assert await tokens.token() == "t", "the token outlives the mistake"


def test_a_key_file_that_cannot_be_read_stops_the_start(tmp_path: Any) -> None:
    with pytest.raises(CredentialsInvalid, match="cannot be read"):
        KeyFile(str(tmp_path / "missing.json"))
    (tmp_path / "bad.json").write_text("not json")
    with pytest.raises(CredentialsInvalid, match="not valid JSON"):
        KeyFile(str(tmp_path / "bad.json"))


def test_the_key_in_two_places_stops_the_start(tmp_path: Any) -> None:
    with pytest.raises(ValueError, match="both set"):
        GatewaySettings(vertex_credentials="{}", vertex_credentials_file=str(tmp_path / "k"))


def test_a_key_file_registers_the_platform(tmp_path: Any) -> None:
    path = tmp_path / "key.json"
    path.write_text(_key_json("svc@p"))

    upstreams = build_vertex_upstreams(
        GatewaySettings(vertex_project="p", vertex_credentials_file=str(path))
    )

    assert upstreams, "a file is a credential like the value"


def test_an_unknown_reason_is_explained_by_googles_status() -> None:
    body = _google(403, "PERMISSION_DENIED", {"reason": "SOMETHING_NEW"})
    body["error"]["details"].insert(0, {"@type": "type.googleapis.com/google.rpc.Help"})
    body["error"]["details"].insert(0, "not an object")

    found = from_body(body, 403)

    assert found is not None
    assert found.reason == "SOMETHING_NEW", "a new word is still the word to report"
    assert "Model Garden" in found.explanation


def test_a_connection_error_without_a_request_names_no_host() -> None:
    found = from_transport(httpx.ConnectError("boom"))

    assert found is not None
    assert "the provider" in found.explanation


async def test_a_token_exchange_without_a_token_is_refused() -> None:
    tokens = _token_endpoint(200, {"expires_in": 3600})
    transport = _transport(lambda request: httpx.Response(200, json={}), tokens=tokens)

    with pytest.raises(UpstreamError) as caught:
        await transport.post(URL, {})

    assert caught.value.status_code == 503


def test_a_key_file_that_is_a_directory_stops_the_start(tmp_path: Any) -> None:
    with pytest.raises(CredentialsInvalid, match="cannot be read"):
        KeyFile(str(tmp_path))
