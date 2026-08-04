from collections.abc import Callable

import httpx
import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.gemini import GeminiUpstream, build_gemini_upstream

Handler = Callable[[httpx.Request], httpx.Response]


def _upstream(handler: Handler) -> GeminiUpstream:
    client = httpx.AsyncClient(
        base_url="https://api.test/v1beta", transport=httpx.MockTransport(handler)
    )
    return GeminiUpstream("secret-key", ["gemini-2.0-flash"], client)


def _request(text: str = "hi") -> CanonicalRequest:
    return CanonicalRequest(
        model="gemini-2.0-flash", messages=[CanonicalMessage(role=Role.USER, text=text)]
    )


async def test_generate_maps_response_and_sends_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models/gemini-2.0-flash:generateContent")
        assert request.url.params["key"] == "secret-key"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "Hi there"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 2},
            },
        )

    response = await _upstream(handler).generate(_request())
    assert response.text == "Hi there"
    assert response.usage.total_tokens == 4


async def test_generate_error_status_is_carried() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "boom"})

    with pytest.raises(UpstreamError, match="returned 429") as exc_info:
        await _upstream(handler).generate(_request())
    assert exc_info.value.status_code == 429


async def test_generate_transport_error_has_no_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(UpstreamError, match="ConnectError") as exc_info:
        await _upstream(handler).generate(_request())
    assert exc_info.value.status_code is None


async def test_embed_returns_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":embedContent")
        return httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3]}})

    assert await _upstream(handler).embed("gemini-2.0-flash", "text") == [0.1, 0.2, 0.3]


async def test_stream_reconstructs_text_and_finish() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = (
            b'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\n\n'
            b'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]},'
            b'"finishReason":"STOP"}],"usageMetadata":'
            b'{"promptTokenCount":1,"candidatesTokenCount":1}}\n\n'
        )
        return httpx.Response(200, content=body)

    chunks = [chunk async for chunk in _upstream(handler).stream_generate(_request())]
    assert "".join(chunk.text_delta for chunk in chunks) == "Hello"
    assert chunks[-1].finish_reason == "stop"


async def test_stream_error_status_is_carried() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"")

    with pytest.raises(UpstreamError, match="returned 503") as exc_info:
        [chunk async for chunk in _upstream(handler).stream_generate(_request())]
    assert exc_info.value.status_code == 503


async def test_stream_transport_error_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(UpstreamError, match="ConnectError"):
        [chunk async for chunk in _upstream(handler).stream_generate(_request())]


def test_models_lists_configured_names() -> None:
    upstream = _upstream(lambda _request: httpx.Response(200, json={}))
    assert [model.name for model in upstream.models()] == ["gemini-2.0-flash"]


def test_build_returns_none_without_key() -> None:
    assert build_gemini_upstream(GatewaySettings(google_api_key="")) is None


def test_build_parses_model_list() -> None:
    upstream = build_gemini_upstream(GatewaySettings(google_api_key="k", gemini_models="a, b ,"))
    assert upstream is not None
    assert [model.name for model in upstream.models()] == ["a", "b"]
