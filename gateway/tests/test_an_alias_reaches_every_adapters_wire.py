"""A catalogue alias is sent by every adapter — not only by the two Vertex ones that had it.

**What this is about.** A catalogue entry may name its upstream model in `addressing.model` and be
an alias of it: the adapter sends that name, and everything else — release, approval, residency,
price, numeric id — is the entry's own. Vertex's Gemini and Claude adapters sent it. The managed
open models on Vertex, Google AI Studio and every OpenAI-compatible server sent the entry's own
name instead, which the vendor does not know: a 404 for every request to an alias the Management
API accepts for any provider.

Every test records what the wire carried, through the real adapter.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    Role,
)
from aira_gateway.upstreams.gemini import GeminiUpstream
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport
from aira_gateway.upstreams.vertex.adapters import VertexOpenModelAdapter
from aira_gateway.upstreams.vertex.transport import VertexTransport

ALIAS = "kira-chat"
UPSTREAM = "real-model-7b"

OPENAI_ANSWER = {
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
OPENAI_STREAM = (
    b'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)
GEMINI_ANSWER = {
    "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
}
GEMINI_STREAM = b"data: " + json.dumps(GEMINI_ANSWER).encode() + b"\n\n"


def _request(**addressing: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model=ALIAS,
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"model": UPSTREAM, **addressing},
    )


def _embedding(*texts: str) -> CanonicalEmbeddingRequest:
    return CanonicalEmbeddingRequest(model=ALIAS, texts=list(texts), addressing={"model": UPSTREAM})


def _recording(seen: list[httpx.Request], answer: Callable[[httpx.Request], httpx.Response]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    return httpx.MockTransport(handler)


async def _drain(chunks: Any) -> None:
    async for _ in chunks:
        pass


# == the managed open models on Vertex ============================================================


def _open_models(seen: list[httpx.Request], answer: Any) -> VertexOpenModelAdapter:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=_recording(seen, answer)),
        allowed_regions=("global",),
    )
    return VertexOpenModelAdapter(
        transport, [], publisher="zai-org", thinking_switch="enable_thinking"
    )


@pytest.mark.parametrize("streamed", [False, True], ids=["generate", "stream"])
async def test_the_open_models_are_sent_the_upstream_name(streamed: bool) -> None:
    seen: list[httpx.Request] = []
    answer = (
        (lambda r: httpx.Response(200, content=OPENAI_STREAM))
        if streamed
        else (lambda r: httpx.Response(200, json=OPENAI_ANSWER))
    )
    adapter = _open_models(seen, answer)

    if streamed:
        await _drain(adapter.stream_generate(_request(regions=["global"])))
    else:
        await adapter.generate(_request(regions=["global"]))

    assert json.loads(seen[0].content)["model"] == f"zai-org/{UPSTREAM}"


async def test_the_open_models_probe_asks_for_the_upstream_name() -> None:
    seen: list[httpx.Request] = []
    adapter = _open_models(seen, lambda r: httpx.Response(400, json={"error": {"message": "x"}}))

    await adapter.ping(ALIAS, {"model": UPSTREAM, "regions": "global"})

    assert json.loads(seen[0].content)["model"] == f"zai-org/{UPSTREAM}"


# == Google AI Studio =============================================================================


def _studio(seen: list[httpx.Request], answer: Any) -> GeminiUpstream:
    client = httpx.AsyncClient(
        base_url="https://studio.test/v1beta", transport=_recording(seen, answer)
    )
    return GeminiUpstream("key", [], client)


async def test_ai_studio_is_sent_the_upstream_name_for_an_answer() -> None:
    seen: list[httpx.Request] = []
    await _studio(seen, lambda r: httpx.Response(200, json=GEMINI_ANSWER)).generate(_request())

    assert seen[0].url.path.endswith(f"/models/{UPSTREAM}:generateContent")


async def test_ai_studio_is_sent_the_upstream_name_for_a_stream() -> None:
    seen: list[httpx.Request] = []
    adapter = _studio(seen, lambda r: httpx.Response(200, content=GEMINI_STREAM))

    await _drain(adapter.stream_generate(_request()))

    assert seen[0].url.path.endswith(f"/models/{UPSTREAM}:streamGenerateContent")


async def test_ai_studio_is_sent_the_upstream_name_for_embeddings() -> None:
    seen: list[httpx.Request] = []
    vector = {"values": [0.1]}
    adapter = _studio(
        seen,
        lambda r: httpx.Response(
            200, json={"embeddings": [vector, vector]} if "batch" in r.url.path else {}
        ),
    )

    await adapter.embed(_embedding("one", "two"))

    assert seen[0].url.path.endswith(f"/models/{UPSTREAM}:batchEmbedContents")
    sent = json.loads(seen[0].content)["requests"]
    assert {entry["model"] for entry in sent} == {f"models/{UPSTREAM}"}


async def test_ai_studios_probe_looks_for_the_upstream_name() -> None:
    seen: list[httpx.Request] = []
    listing = {"models": [{"name": f"models/{UPSTREAM}"}]}
    adapter = _studio(seen, lambda r: httpx.Response(200, json=listing))

    assert await adapter.ping(ALIAS, {"model": UPSTREAM}) == f"{UPSTREAM} is listed"


# == an OpenAI-compatible server ==================================================================


def _server(seen: list[httpx.Request], answer: Any) -> OpenAIAdapter:
    client = httpx.AsyncClient(base_url="http://gpu.test", transport=_recording(seen, answer))
    return OpenAIAdapter(OpenAITransport(client=client), [], provider="gpu-a")


@pytest.mark.parametrize("streamed", [False, True], ids=["generate", "stream"])
async def test_an_openai_compatible_server_is_sent_the_upstream_name(streamed: bool) -> None:
    seen: list[httpx.Request] = []
    answer = (
        (lambda r: httpx.Response(200, content=OPENAI_STREAM))
        if streamed
        else (lambda r: httpx.Response(200, json=OPENAI_ANSWER))
    )
    adapter = _server(seen, answer)

    if streamed:
        await _drain(adapter.stream_generate(_request()))
    else:
        await adapter.generate(_request())

    assert json.loads(seen[0].content)["model"] == UPSTREAM


async def test_an_openai_compatible_server_is_sent_the_upstream_name_for_embeddings() -> None:
    seen: list[httpx.Request] = []
    vectors = {"data": [{"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 1}}
    adapter = _server(seen, lambda r: httpx.Response(200, json=vectors))

    await adapter.embed(_embedding("one"))

    assert json.loads(seen[0].content)["model"] == UPSTREAM


async def test_an_openai_compatible_servers_probe_looks_for_the_upstream_name() -> None:
    seen: list[httpx.Request] = []
    adapter = _server(seen, lambda r: httpx.Response(200, json={"data": [{"id": UPSTREAM}]}))

    assert await adapter.ping(ALIAS, {"model": UPSTREAM}) == f"{UPSTREAM} is listed"
