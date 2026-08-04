from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _BoomProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("upstream exploded with a secret detail")

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("boom")
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, model, text):  # noqa: ANN001, ANN201
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


class _UnavailableProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream is down")

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream is down")
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, model, text):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream is down")


def _unavailable_client() -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_UnavailableProvider()])
    return TestClient(app, raise_server_exceptions=False)


def test_upstream_error_on_generate_returns_502() -> None:
    with _unavailable_client() as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["status"] == "UNAVAILABLE"
    assert body["error"]["message"] == "upstream is down"


def test_upstream_error_on_embed_returns_502() -> None:
    with _unavailable_client() as client:
        resp = client.post(
            "/v1beta/models/mock-1:embedContent",
            json={"content": {"parts": [{"text": "hi"}]}},
        )
    assert resp.status_code == 502
    assert resp.json()["error"]["status"] == "UNAVAILABLE"


def test_upstream_error_on_stream_terminates_cleanly() -> None:
    with _unavailable_client() as client:
        resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
    # Stream headers are already sent when the upstream fails: status stays 200, body is an
    # empty JSON array (the error is logged server-side, never leaked to the client).
    assert resp.status_code == 200
    assert resp.text == "[]"


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
