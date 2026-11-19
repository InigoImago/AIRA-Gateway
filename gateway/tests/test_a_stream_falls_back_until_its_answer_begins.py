"""A streamed request falls back — to the next region, and down the chain — until its answer
begins: its first chunk that carries something, not the `200` before it.

**What this is about.** Nothing reaches the caller before a model's first word, so until then a
failing region or candidate can be replaced without anybody seeing it — the rule the chain and the
regional failover are both built on. Two places took the moment too early:

- **a region was committed at its `200`.** An error the provider sends inside the stream before
  its first word — Anthropic's `overloaded_error` right after `message_start`, Google's in-band
  `503` — then never reached the next region, although the same failure unstreamed did. A model is
  usually catalogued in several regions so that one region's outage costs nothing, and the verb
  every chat client uses lost exactly that;
- **a candidate was committed by its preamble.** OpenAI-compatible servers — vLLM, Azure, OpenAI
  itself — begin every stream with an empty role delta. Read as the first chunk, it committed the
  chain to a server that then failed before its first token, and the configured fallback was never
  asked although the caller had received nothing.

Every test drives a real adapter over an `httpx.MockTransport`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
)
from aira_gateway.pipeline.dispatch import stream_with_fallback
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, read_opening
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport
from aira_gateway.upstreams.vertex import VertexModel, VertexTransport
from aira_gateway.upstreams.vertex.adapters import (
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexOpenModelAdapter,
)

REGIONS = ["europe-west1", "europe-west4"]
MODEL, BACKUP = "primary-1", "backup-1"

CLAUDE_START = (
    b'event: message_start\ndata: {"type":"message_start","message":'
    b'{"usage":{"input_tokens":900,"output_tokens":1}}}\n\n'
)
CLAUDE_OVERLOADED = (
    b'event: error\ndata: {"type":"error","error":'
    b'{"type":"overloaded_error","message":"Overloaded"}}\n\n'
)
CLAUDE_FIRST_WORDS = (
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"served by west4"}}\n\n'
)
CLAUDE_END = (
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":4}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
GEMINI_UNAVAILABLE = (
    b'data: {"error":{"code":503,"message":"The model is overloaded.","status":"UNAVAILABLE"}}\n\n'
)
GEMINI_ANSWER = (
    b'data: {"candidates":[{"content":{"parts":[{"text":"served by west4"}]},'
    b'"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":3}}\n\n'
)

#: What an OpenAI-compatible server sends first, before any token: the role, and no content.
PREAMBLE = (
    b'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,'
    b'"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
)
ENGINE_FAILED = (
    b'data: {"error":{"message":"CUDA out of memory","type":"server_error","code":500}}\n\n'
)


def _regions(failing: bytes, healthy: bytes, contacted: list[str]) -> VertexTransport:
    """A real Vertex transport whose first region sends ``failing`` inside a `200`."""

    def handler(request: httpx.Request) -> httpx.Response:
        region = request.url.host.split("-aiplatform")[0]
        contacted.append(region)
        body = failing if region == REGIONS[0] else healthy
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=tuple(REGIONS),
    )


def _request(model: str, regions: list[str] | None = None) -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"regions": regions} if regions else {},
    )


def _claude(transport: VertexTransport) -> VertexAnthropicAdapter:
    return VertexAnthropicAdapter(transport, [], default_max_tokens=1024)


def _gemini(transport: VertexTransport) -> VertexGeminiAdapter:
    return VertexGeminiAdapter(transport, [])


# == the next region ==============================================================================


@pytest.mark.parametrize(
    ("build", "failing", "healthy"),
    [
        (_claude, CLAUDE_START + CLAUDE_OVERLOADED, CLAUDE_START + CLAUDE_FIRST_WORDS + CLAUDE_END),
        (_gemini, GEMINI_UNAVAILABLE, GEMINI_ANSWER),
    ],
    ids=["claude-overloaded-after-message_start", "gemini-503-in-band"],
)
async def test_a_region_that_fails_before_its_first_word_is_passed_over(
    build: Any, failing: bytes, healthy: bytes
) -> None:
    contacted: list[str] = []
    adapter = build(_regions(failing, healthy, contacted))

    chunks = [chunk async for chunk in adapter.stream_generate(_request("model-x", REGIONS))]

    assert "".join(chunk.text_delta for chunk in chunks) == "served by west4"
    assert chunks[0].served_region == "europe-west4", "the stream names the region it came from"
    assert contacted == REGIONS


async def test_the_same_failure_unstreamed_was_always_passed_over() -> None:
    """The comparison: the outcome must not depend on whether the client streamed."""
    contacted: list[str] = []
    adapter = _claude(
        _regions(
            CLAUDE_START + CLAUDE_OVERLOADED,
            CLAUDE_START + CLAUDE_FIRST_WORDS + CLAUDE_END,
            contacted,
        )
    )

    answer = await adapter.generate(_request("model-x", REGIONS))

    assert (answer.text, answer.served_region) == ("served by west4", "europe-west4")
    assert contacted == REGIONS


async def test_a_region_that_fails_after_its_first_word_is_not_replaced() -> None:
    """**The limit, asserted.** Once the answer has begun the caller holds words from this region,
    and another region would continue them with a first sentence of its own."""
    contacted: list[str] = []
    adapter = _claude(
        _regions(
            CLAUDE_START + CLAUDE_FIRST_WORDS + CLAUDE_OVERLOADED,
            CLAUDE_START + CLAUDE_FIRST_WORDS + CLAUDE_END,
            contacted,
        )
    )
    received: list[str] = []

    with pytest.raises(UpstreamError, match="overloaded_error"):
        async for chunk in adapter.stream_generate(_request("model-x", REGIONS)):
            received.append(chunk.text_delta)

    assert received == ["served by west4"]
    assert contacted == REGIONS[:1]


# == the next candidate ===========================================================================


def _sse(body: bytes) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )
    )


def _openai(body: bytes) -> OpenAIAdapter:
    client = httpx.AsyncClient(base_url="http://vllm.test", transport=_sse(body))
    return OpenAIAdapter(OpenAITransport(client=client), [MODEL])


def _open_models(body: bytes) -> VertexOpenModelAdapter:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=_sse(body)),
        allowed_regions=("europe-west1",),
        model_regions={MODEL: frozenset({"global"})},
    )
    return VertexOpenModelAdapter(
        transport,
        [VertexModel("global", "zai-org", MODEL)],
        publisher="zai-org",
        thinking_switch="enable_thinking",
    )


async def _text(chunks: Any) -> str:
    return "".join([chunk.text_delta async for chunk in chunks])


@pytest.mark.parametrize("build", [_openai, _open_models], ids=["openai", "vertex-open-models"])
async def test_a_failure_right_after_the_preamble_moves_the_chain_on(build: Any) -> None:
    """The project's own test of this sent the error as the **first** payload, which no
    preamble-sending server does — so it passed while every real one committed the chain."""
    registry = ProviderRegistry([build(PREAMBLE + ENGINE_FAILED), MockProvider(BACKUP)])

    streamed = await stream_with_fallback(registry, _request(MODEL), (BACKUP,))

    assert streamed.model == BACKUP
    assert [entry.model for entry in streamed.failed] == [MODEL]
    assert (await _text(streamed.chunks)).strip()


@pytest.mark.parametrize("build", [_openai, _open_models], ids=["openai", "vertex-open-models"])
async def test_a_stream_that_ends_after_its_preamble_has_answered(build: Any) -> None:
    """**What stays as it was:** a stream that ends — with nothing, or only a preamble — is the
    vendor's answer, not an outage. A safety refusal looks like it, and asking the chain again
    pays every model to refuse the same request."""
    registry = ProviderRegistry([build(PREAMBLE + b"data: [DONE]\n\n"), MockProvider(BACKUP)])

    streamed = await stream_with_fallback(registry, _request(MODEL), (BACKUP,))

    assert streamed.model == MODEL
    assert streamed.failed == []
    assert await _text(streamed.chunks) == ""


async def test_what_the_preamble_reported_still_reaches_the_caller() -> None:
    """Held while the candidate proves itself, not dropped: a server reporting usage on every chunk
    reports it on the preamble too, and the running total is what a stream that breaks off later is
    booked at."""
    preamble_with_usage = (
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}],'
        b'"usage":{"prompt_tokens":7,"completion_tokens":0,"total_tokens":7}}\n\n'
    )
    words = b'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
    registry = ProviderRegistry([_openai(preamble_with_usage + words), MockProvider(BACKUP)])

    streamed = await stream_with_fallback(registry, _request(MODEL), (BACKUP,))
    chunks = [chunk async for chunk in streamed.chunks]

    assert streamed.model == MODEL
    assert [chunk.usage.prompt_tokens for chunk in chunks if chunk.usage] == [7]
    assert "".join(chunk.text_delta for chunk in chunks) == "hello"


async def test_a_long_preamble_is_held_as_one_chunk() -> None:
    """A model that thinks for minutes without showing it sends a delta per thought, each carrying
    nothing the caller is shown; held one by one, a long silence would be a long list. Held as one,
    with the latest of their running totals."""

    async def thinking_quietly() -> Any:
        for step in range(10_000):
            yield CanonicalChunk(
                text_delta="", usage=CanonicalUsage(prompt_tokens=5, completion_tokens=step)
            )
        yield CanonicalChunk(text_delta="answer")

    opening = await read_opening(thinking_quietly())

    assert [chunk.text_delta for chunk in opening] == ["", "answer"]
    assert opening[0].usage == CanonicalUsage(prompt_tokens=5, completion_tokens=9_999)
