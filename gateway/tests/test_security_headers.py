"""The response headers a JSON API owes a browser (2026-08-08).

Management has had these since `ADR-0007`; the gateway had none. "It is not a browser-facing
service" was the argument, and it was wrong: the console's dry-run, consumption, reporting, traces
and incident views all call it from a browser through the `/gw` proxy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, test_database=True)))


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "no-referrer"),
        ("x-frame-options", "DENY"),
        ("cache-control", "no-store"),
    ],
)
def test_every_response_carries_them(client: TestClient, header: str, value: str) -> None:
    response = client.get("/healthz")

    assert response.headers[header] == value


def test_a_refusal_carries_them_too(client: TestClient) -> None:
    """The responses that most need them are the ones that went wrong — a reflected error message
    is exactly the body a sniffing browser could be talked into rendering."""
    response = client.post("/v1beta/models/nope:generateContent", json={"contents": []})

    assert response.status_code >= 400
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_credential_in_the_url_is_not_leaked_by_referer(client: TestClient) -> None:
    """Every Gemini client may authenticate with `?key=<api key>`. Without `Referrer-Policy` a
    browser hands that URL to the next origin it visits."""
    response = client.get("/healthz?key=aira_abcd_secret")

    assert response.headers["referrer-policy"] == "no-referrer"
