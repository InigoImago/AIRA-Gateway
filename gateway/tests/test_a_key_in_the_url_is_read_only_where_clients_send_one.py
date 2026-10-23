"""`?key=` is Google's convention, and only the surface that speaks Google's API reads it.

A key in a URL is written into the access log of every proxy it passes. Gemini clients send one
there because Google's API takes it, so the Gemini surface does too. The KIRA contract
authenticates with a bearer token, and none of its clients ever put a key in a URL — so on that
surface the URL is not read, and a caller who put the key there is told so, by name, rather than
"missing" about a key they sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_gateway.auth import keys
from aira_gateway.auth.credentials import KEY_IN_URL

KIRA_CHAT = "/kira/api/external/chat"
GEMINI = "/v1beta/models/mock-1:generateContent"
KIRA_BODY: dict[str, Any] = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1004}
GEMINI_BODY: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


@pytest.mark.parametrize("path", [KIRA_CHAT, f"/uc/demo-uc{KIRA_CHAT}"])
def test_a_kira_request_with_its_key_in_the_url_is_refused_by_name(authed_client, path) -> None:  # noqa: ANN001
    response = authed_client.post(f"{path}?key={keys.DEMO_API_KEY}", json=KIRA_BODY)

    assert response.status_code == 401, response.text
    # "None was presented where this surface reads one": the contract's code for an absent
    # credential, with the reason in the message.
    assert response.json()["code"] == "NOT_AUTHENTICATED"
    assert response.json()["message"] == KEY_IN_URL
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_the_same_key_in_a_header_is_read(authed_client) -> None:  # noqa: ANN001
    """The comparison that makes the refusal about *where*, not about the key."""
    response = authed_client.post(
        KIRA_CHAT, json=KIRA_BODY, headers={"x-goog-api-key": keys.DEMO_API_KEY}
    )

    assert response.status_code != 401, response.text


def test_a_header_credential_still_wins_over_a_key_in_the_url(authed_client) -> None:  # noqa: ANN001
    """The URL is not read on this surface whatever else is sent; a request authenticated by its
    header is not refused for a stray parameter."""
    response = authed_client.post(
        f"{KIRA_CHAT}?key=aira_dead_beef",
        json=KIRA_BODY,
        headers={"authorization": f"Bearer {keys.DEMO_API_KEY}"},
    )

    assert response.status_code != 401, response.text


def test_the_gemini_surface_reads_a_key_in_the_url(authed_client) -> None:  # noqa: ANN001
    """Google's convention, which Google's clients follow: unchanged."""
    response = authed_client.post(f"{GEMINI}?key={keys.DEMO_API_KEY}", json=GEMINI_BODY)

    assert response.status_code == 200, response.text


def test_a_kira_health_check_with_a_key_in_the_url_gets_the_verdict(authed_client) -> None:  # noqa: ANN001
    """`/health` answers without a credential; a key in its URL is not one either."""
    response = authed_client.get(f"/kira/api/external/health?key={keys.DEMO_API_KEY}")

    assert response.status_code in (200, 503), response.text
