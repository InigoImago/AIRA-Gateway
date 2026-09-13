"""A caller's mistake is refused before dispatch, naming the field (`FRD-124`).

A role Gemini does not have, a response media type this gateway cannot honour and a sampling value
outside every dialect's range each reached a real model: the first was read as the user's own turn,
the second was ignored, the third was refused upstream and recorded as the provider's error.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import SAMPLING as GEMINI_SAMPLING

_URL = "/v1beta/models/mock-1:generateContent"


class _Counting:
    """Answers anything and counts, so a refusal can be shown to have reached nobody."""

    is_test_double = True
    sampling_controls = GEMINI_SAMPLING

    def __init__(self) -> None:
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        return CanonicalResponse(
            model="mock-1", text="ok", usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1)
        )


def _app() -> tuple[FastAPI, _Counting]:
    provider = _Counting()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([provider])
    return app, provider


def _body(role: str | None = "user", **config: Any) -> dict[str, Any]:
    content: dict[str, Any] = {"parts": [{"text": "hi"}]}
    if role is not None:
        content["role"] = role
    body: dict[str, Any] = {"contents": [content]}
    if config:
        body["generationConfig"] = config
    return body


# == roles =========================================================================================


@pytest.mark.parametrize("role", ["assistant", "banana", "USER"])
def test_a_role_gemini_does_not_have_is_refused(role: str) -> None:
    app, provider = _app()
    with TestClient(app) as client:
        response = client.post(_URL, json=_body(role))

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    assert f"'{role}'" in response.json()["error"]["message"]
    assert provider.calls == 0


@pytest.mark.parametrize("role", ["user", "function", None])
def test_the_roles_google_clients_send_are_served(role: str | None) -> None:
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post(_URL, json=_body(role))

    assert response.status_code == 200, response.text


# == the response media type =======================================================================


@pytest.mark.parametrize(
    ("config", "named"),
    [
        ({"responseMimeType": "text/xml"}, "text/xml"),
        ({"responseMimeType": "application/json"}, "responseSchema"),
    ],
    ids=["another-type", "json-without-a-schema"],
)
def test_a_response_media_type_this_gateway_cannot_honour_is_refused(
    config: dict[str, Any], named: str
) -> None:
    app, provider = _app()
    with TestClient(app) as client:
        response = client.post(_URL, json=_body(**config))

    assert response.status_code == 400
    assert named in response.json()["error"]["message"]
    assert provider.calls == 0


def test_plain_text_is_served() -> None:
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post(_URL, json=_body(responseMimeType="text/plain"))

    assert response.status_code == 200, response.text


# == sampling ranges ===============================================================================


@pytest.mark.parametrize(
    ("config", "named"),
    [
        ({"temperature": 5}, "temperature"),
        ({"temperature": -1}, "temperature"),
        ({"topP": 1.5}, "topP"),
        ({"topK": 0}, "topK"),
        ({"presencePenalty": 3}, "presencePenalty"),
        ({"frequencyPenalty": -2.5}, "frequencyPenalty"),
    ],
)
async def test_a_sampling_value_outside_every_dialects_range_is_refused_before_dispatch(
    config: dict[str, Any], named: str
) -> None:
    app, provider = _app()
    with TestClient(app) as client:
        response = client.post(_URL, json=_body(**config))
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    assert named in response.json()["error"]["message"]
    assert provider.calls == 0, "refused before any provider was asked"
    assert [row.outcome for row in rows] == ["invalid_request"], "the caller's, not the provider's"


def test_the_edges_of_each_range_are_served() -> None:
    app, provider = _app()
    with TestClient(app) as client:
        response = client.post(
            _URL,
            json=_body(temperature=2, topP=1, topK=1, presencePenalty=-2, frequencyPenalty=2),
        )

    assert response.status_code == 200, response.text
    assert provider.calls == 1


async def test_the_kira_surface_refuses_it_too() -> None:
    app, provider = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", numeric_id=1, capabilities=["generate"]))
            await session.commit()
        response = client.post(
            "/kira/api/external/chat",
            json={"request": {"parts": [{"text": "hi"}]}, "model_id": 1, "temperature": 5},
        )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "temperature" in response.json()["message"]
    assert provider.calls == 0
