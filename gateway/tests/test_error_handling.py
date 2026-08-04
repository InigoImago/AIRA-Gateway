from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _BoomProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    def generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("upstream exploded with a secret detail")

    def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("boom")

    def embed(self, model, text):  # noqa: ANN001, ANN201
        raise RuntimeError("boom")


def test_unexpected_error_on_api_returns_gemini_500() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_BoomProvider()])
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["status"] == "INTERNAL"
    assert body["error"]["code"] == 500
    # the raw exception detail must not leak to the client
    assert "secret detail" not in resp.text


def test_unexpected_error_on_non_api_returns_envelope() -> None:
    app = create_app(GatewaySettings(auth_required=False))

    @app.get("/kaboom")
    async def _kaboom() -> None:
        raise RuntimeError("internal secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/kaboom")

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "internal secret" not in resp.text
