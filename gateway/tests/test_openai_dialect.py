"""The third wire dialect (FRD-123, and FRD-120 later).

Hermetic throughout: an ``httpx.MockTransport`` stands in for the endpoint, so the bodies, the
streaming assembly and the error mapping are exercised without a model. That split is the point —
**this** is where the adapter is proved, and the live model in `tests/integration/` is what stops
us proving the wrong thing.

Two properties carry the weight, and both are cases where the other two dialects taught us what to
look for:

- **Usage arrives in a chunk with no choices.** Anthropic split usage across two events and a
  last-event-wins mapper silently reported zero input tokens for every stream. This format has the
  same trap wearing different clothes, and a mapper indexing ``choices[0]`` would lose the counts.
- **A token budget has no equivalent here at all.** `FRD-111` §5.2 predicted this before the
  dialect existed; rounding 20 000 tokens to "high" would spend a different amount of money than
  was asked for and nothing about the answer would show it.
"""

from __future__ import annotations

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
    DataPart,
    Role,
    TextPart,
    Thinking,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.openai import build_local_upstream
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.mapping import (
    DialectUnsupported,
    canonical_to_openai,
    canonical_to_openai_embedding,
    embedding_values,
    openai_chunk_to_canonical,
    openai_to_canonical,
    parse_sse_line,
)
from aira_gateway.upstreams.openai.transport import OpenAITransport

Handler = Callable[[httpx.Request], httpx.Response]
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


def _adapter(handler: Handler, **kwargs: object) -> OpenAIAdapter:
    client = httpx.AsyncClient(base_url="http://local.test", transport=httpx.MockTransport(handler))
    return OpenAIAdapter(OpenAITransport(client=client), ["local-1"], **kwargs)  # type: ignore[arg-type]


def _request(**over: object) -> CanonicalRequest:
    return CanonicalRequest(
        model="local-1",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.USER, text="hello"),
        ],
        **over,  # type: ignore[arg-type]
    )


# == the request body ============================================================================


def test_the_system_prompt_is_just_the_first_message() -> None:
    """The one place this dialect is simpler than the other two: no split-out system parameter."""
    body = canonical_to_openai(_request())
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "be brief"


def test_a_text_only_message_stays_a_plain_string() -> None:
    """Several implementations of this API are fussier about the array form, and a body full of
    single-element arrays is harder to read in a log for no gain."""
    assert isinstance(canonical_to_openai(_request())["messages"][1]["content"], str)


def test_an_image_becomes_a_data_uri_part() -> None:
    request = _request()
    request.messages[1].parts = [
        TextPart(text="what is this"),
        DataPart(media_type="image/png", data=PNG),
    ]
    content = canonical_to_openai(request)["messages"][1]["content"]

    assert [part["type"] for part in content] == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_document_is_refused_rather_than_sent_as_an_image() -> None:
    """`ADR-0012`'s central case: GPT-shaped models read images and **not** documents. The chain
    should have skipped this candidate, so reaching here means a catalog claims a capability the
    dialect cannot deliver — and sending the prompt without the document returns a confident wrong
    answer with a 200 on it."""
    request = _request()
    request.messages[1].parts = [DataPart(media_type="application/pdf", data=b"%PDF-1.7")]

    with pytest.raises(DialectUnsupported, match="images"):
        canonical_to_openai(request)


def test_the_output_cap_and_temperature_are_carried() -> None:
    body = canonical_to_openai(_request(max_output_tokens=64, temperature=0.2))
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2


def test_a_request_with_no_options_carries_none_of_them() -> None:
    body = canonical_to_openai(_request())
    assert set(body) == {"model", "messages"}


# == thinking, which this vendor expresses differently ===========================================


@pytest.mark.parametrize(
    ("mode", "effort"),
    [
        (ThinkingMode.MINIMAL, "minimal"),
        (ThinkingMode.LOW, "low"),
        (ThinkingMode.MEDIUM, "medium"),
        (ThinkingMode.HIGH, "high"),
        (ThinkingMode.AUTO, "medium"),
    ],
)
def test_an_effort_level_maps_to_reasoning_effort(mode: ThinkingMode, effort: str) -> None:
    body = canonical_to_openai(_request(thinking=Thinking(mode=mode)))
    assert body["reasoning_effort"] == effort


def test_disabled_thinking_sends_no_parameter() -> None:
    """There is no "off" value; the absence of the parameter is off."""
    body = canonical_to_openai(_request(thinking=Thinking(mode=ThinkingMode.DISABLED, tokens=0)))
    assert "reasoning_effort" not in body


def test_a_token_budget_is_refused_rather_than_rounded_to_a_level() -> None:
    """`FRD-111` §5.2 predicted this before the dialect existed. Rounding a caller's 20 000-token
    budget to "high" spends a different amount of money than they asked for, and nothing about the
    answer would show it — so the request fails instead."""
    setting = Thinking(mode=ThinkingMode.LIMITED, tokens=20_000)
    with pytest.raises(DialectUnsupported, match="effort level"):
        canonical_to_openai(_request(thinking=setting))


# == structured output — the third mechanism =====================================================


def test_a_schema_becomes_a_named_strict_json_schema() -> None:
    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    body = canonical_to_openai(_request(response_schema=schema))

    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"]  # the vendor requires one
    assert fmt["json_schema"]["schema"]["properties"]["a"]["type"] == "string"


# == the response ================================================================================


def test_a_completion_maps_back_with_its_usage() -> None:
    payload = {
        "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    response = openai_to_canonical(payload, "local-1")

    assert response.text == "hi there"
    assert response.finish_reason == "stop"
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (7, 3)


def test_length_means_truncation_not_a_normal_stop() -> None:
    """`FRD-112` FR-6 turns on this distinction: a schema-constrained answer that stopped at the
    cap is not a document, and reporting it as a normal stop would return half of one as data."""
    payload = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}], "usage": {}}
    assert openai_to_canonical(payload, "local-1").finish_reason == "max_tokens"


def test_an_empty_response_does_not_raise() -> None:
    """A compatible server that answers `{}` is a configuration problem, not a crash."""
    assert openai_to_canonical({}, "local-1").text == ""


# == streaming ===================================================================================


def test_usage_arrives_in_a_chunk_with_no_choices() -> None:
    """The trap this format shares with Anthropic's, wearing different clothes. A mapper that
    indexed `choices[0]` unconditionally would lose the token counts of **every** streamed
    request — and a stream reporting no usage is *released* rather than settled (`FRD-405`), so
    every streamed request would silently become free."""
    chunk = openai_chunk_to_canonical({"choices": [], "usage": {"prompt_tokens": 5}})
    assert chunk is not None
    assert chunk.usage is not None and chunk.usage.prompt_tokens == 5


def test_a_chunk_with_neither_content_nor_usage_is_dropped() -> None:
    assert openai_chunk_to_canonical({"choices": []}) is None


def test_a_delta_becomes_a_text_chunk() -> None:
    chunk = openai_chunk_to_canonical({"choices": [{"delta": {"content": "Hel"}}]})
    assert chunk is not None and chunk.text_delta == "Hel"


@pytest.mark.parametrize("line", ["data: [DONE]", "data:", ": keep-alive", "event: ping"])
def test_the_sentinel_and_the_noise_parse_to_nothing(line: str) -> None:
    assert parse_sse_line(line) is None


async def test_a_stream_reconstructs_the_text_and_the_final_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Without this, the vendor reports no usage for a streamed response at all.
        assert body["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    chunks = [chunk async for chunk in _adapter(handler).stream_generate(_request())]

    assert "".join(chunk.text_delta for chunk in chunks) == "Hello"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 6


# == embeddings ==================================================================================


def test_a_batch_is_one_call_with_the_whole_input() -> None:
    request = CanonicalEmbeddingRequest(model="local-1", texts=["a", "b"], dimensions=768)
    body = canonical_to_openai_embedding(request)

    assert body["input"] == ["a", "b"]
    assert body["dimensions"] == 768


def test_vectors_come_back_in_the_order_submitted_not_the_order_received() -> None:
    """`index` exists precisely because the API does not promise an order, and "the order
    submitted" is the contract `FRD-113` FR-1 makes to the caller."""
    data = {
        "data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]
    }
    assert embedding_values(data) == [[0.1], [0.2]]


async def test_the_adapter_embeds_through_the_embeddings_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.25]}]})

    request = CanonicalEmbeddingRequest(model="local-1", texts=["hi"])
    assert await _adapter(handler).embed(request) == [[0.5, 0.25]]


# == errors and registration =====================================================================


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_an_upstream_status_is_passed_through(status: int) -> None:
    """A 429 from a self-deployed endpoint means *no free replica* rather than quota
    (`ADR-0012` §5). Flattening it here would remove the reader's ability to tell them apart."""
    adapter = _adapter(lambda request: httpx.Response(status))
    with pytest.raises(UpstreamError) as caught:
        await adapter.generate(_request())
    assert caught.value.status_code == status


async def test_a_transport_failure_is_an_upstream_error_without_a_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError) as caught:
        await _adapter(handler).generate(_request())
    assert caught.value.status_code is None


async def test_a_streaming_failure_is_raised_before_the_caller_iterates() -> None:
    """A 500 that only surfaced on the first `aiter_lines` would arrive after our own response
    headers had already gone out — at which point the status can no longer be changed."""
    adapter = _adapter(lambda request: httpx.Response(500))
    with pytest.raises(UpstreamError):
        async for _ in adapter.stream_generate(_request()):
            pass


def test_the_two_verb_sets_are_disjoint() -> None:
    """A chat model has no embedding endpoint and vice versa. Advertising both for everything
    would leave the catalog declaration as the only thing between a caller and a vendor error."""
    adapter = _adapter(
        lambda request: httpx.Response(200), embedding_models=["embed-1"], region="on-premises"
    )
    described = {model.name: model for model in adapter.models()}

    assert "generateContent" in described["local-1"].supported_methods
    assert "embedContent" not in described["local-1"].supported_methods
    assert "embedContent" in described["embed-1"].supported_methods
    assert "generateContent" not in described["embed-1"].supported_methods


def test_every_model_records_where_it_ran() -> None:
    """A self-hosted model is the strongest residency story available, and one nothing records is
    a claim rather than evidence (`FRD-123` §5.3)."""
    adapter = _adapter(lambda request: httpx.Response(200), region="on-premises")
    described = adapter.models()[0]

    assert described.provider == "openai-compatible" or described.provider
    assert described.region == "on-premises"


def test_no_url_registers_no_adapter() -> None:
    """A verification tool that appears in a deployment nobody asked for it in eventually serves
    production traffic."""
    assert build_local_upstream(GatewaySettings()) == []


def test_a_url_with_no_models_registers_nothing_either() -> None:
    settings = GatewaySettings(ollama_url="http://localhost:11434")
    assert build_local_upstream(settings) == []


def test_a_configured_endpoint_registers_both_model_kinds() -> None:
    settings = GatewaySettings(
        ollama_url="http://localhost:11434",
        ollama_models="qwen3:0.6b",
        ollama_embedding_models="all-minilm",
        ollama_region="on-premises",
    )
    described = {model.name: model for model in build_local_upstream(settings)[0].models()}

    assert set(described) == {"qwen3:0.6b", "all-minilm"}
    assert described["qwen3:0.6b"].provider == "ollama"
    assert described["qwen3:0.6b"].region == "on-premises"
