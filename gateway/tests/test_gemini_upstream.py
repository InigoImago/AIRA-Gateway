import json
from collections.abc import Callable

import httpx
import pytest

from aira_common.models import ThinkingMode
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    Role,
    Thinking,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.gemini import GeminiUpstream, build_gemini_upstream
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request

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

    request = CanonicalEmbeddingRequest(model="gemini-2.0-flash", texts=["text"])
    assert await _upstream(handler).embed(request) == [[0.1, 0.2, 0.3]]


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


# == the options on the wire (FRD-111, FRD-112, FRD-113) =========================================
#
# These assert the *body Google receives*, because that is the only place a mapping mistake shows
# up. A gateway that resolved a thinking budget correctly and then forgot to put it in the request
# would pass every test above this line.


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (Thinking(mode=ThinkingMode.DISABLED, tokens=0), 0),
        (Thinking(mode=ThinkingMode.AUTO, tokens=None), -1),
        (Thinking(mode=ThinkingMode.LIMITED, tokens=4096), 4096),
        # An abstract level arrives already translated by the catalog, so what reaches the wire is
        # a number — "medium" means nothing to an HTTP call.
        (Thinking(mode=ThinkingMode.MEDIUM, tokens=2048), 2048),
    ],
)
def test_each_thinking_mode_maps_to_googles_budget(setting: Thinking, expected: int) -> None:
    body = canonical_to_gemini_request(_request().model_copy(update={"thinking": setting}))
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == expected


def test_an_auto_mode_ignores_a_resolved_budget_on_the_wire() -> None:
    """`auto` means "the model decides", and Google spells that `-1`. Sending the conservative
    figure the reservation used would turn a hint into a cap the caller never asked for."""
    setting = Thinking(mode=ThinkingMode.AUTO, tokens=16_000)
    body = canonical_to_gemini_request(_request().model_copy(update={"thinking": setting}))
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == -1


def test_a_schema_always_travels_with_its_media_type() -> None:
    """`responseSchema` without `responseMimeType` is *ignored* by the API — the model answers in
    prose and the caller parses it as JSON. Our own request body would be the silent failure."""
    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    body = canonical_to_gemini_request(_request().model_copy(update={"response_schema": schema}))

    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == schema.to_wire()


def test_a_request_with_no_options_carries_no_generation_config() -> None:
    """A body that grew empty sub-objects would be a different request to every provider that
    treats "present but default" differently from "absent"."""
    assert "generationConfig" not in canonical_to_gemini_request(_request())


async def test_a_batch_uses_the_batch_endpoint_and_keeps_the_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":batchEmbedContents")
        payload = json.loads(request.content)
        # Google requires the model on each entry, and the order of `requests` is the order of the
        # returned embeddings — which is the contract FR-1 makes to the caller.
        assert [entry["content"]["parts"][0]["text"] for entry in payload["requests"]] == ["a", "b"]
        assert payload["requests"][0]["model"] == "models/gemini-2.0-flash"
        assert payload["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
        return httpx.Response(200, json={"embeddings": [{"values": [0.1]}, {"values": [0.2]}]})

    request = CanonicalEmbeddingRequest(
        model="gemini-2.0-flash", texts=["a", "b"], task_type="RETRIEVAL_DOCUMENT"
    )
    assert await _upstream(handler).embed(request) == [[0.1], [0.2]]


async def test_a_single_text_keeps_using_the_lower_latency_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":embedContent")
        assert json.loads(request.content)["outputDimensionality"] == 768
        return httpx.Response(200, json={"embedding": {"values": [0.5]}})

    request = CanonicalEmbeddingRequest(model="gemini-2.0-flash", texts=["only"], dimensions=768)
    assert await _upstream(handler).embed(request) == [[0.5]]
