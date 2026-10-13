"""An upstream that fails says so, on every platform, however it fails (`FRD-129`, `ADR-0032`).

**What this is about.** Three transports judged their own answers and drifted apart, and three
stream readers each read an error as something else:

- an error the provider sends **inside** a 200 stream — Anthropic's `event: error`, Google's and
  the OpenAI dialect's `{"error": …}` payload — was read as nothing, so the stream simply ended: no
  exception, no finish reason, no usage, the configured fallback never tried, and the audit row
  saying the caller had left;
- a connection lost while a stream was read, a stream line that is not JSON, and a 200 whose body
  is not JSON escaped as the HTTP library's or the parser's own exception — a 500 that blamed the
  gateway, with the fallback never tried — on two of the three transports;
- a 400's stated reason survived on one transport streamed and not, on one only when not streamed,
  and on one never.

Every test here drives a **real adapter** over an `httpx.MockTransport`. Every chain test before
this file used a stand-in that raised `UpstreamError` itself, which proved the chain catches what
the stand-in throws and nothing about what the adapters throw.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.dispatch import dispatch_with_fallback, stream_with_fallback
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, Upstream, UpstreamError
from aira_gateway.upstreams.gemini import GeminiUpstream
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport
from aira_gateway.upstreams.vertex import VertexModel, VertexTransport
from aira_gateway.upstreams.vertex.adapters import (
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexOpenModelAdapter,
)

Handler = Callable[[httpx.Request], httpx.Response]

#: The model each platform under test serves, and the stand-by every chain falls back to.
MODEL = "primary-1"
BACKUP = "backup-1"


def _vertex_gemini(handler: Handler) -> Upstream:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=("europe-west1",),
    )
    return VertexGeminiAdapter(transport, [VertexModel("europe-west1", "google", MODEL)])


def _vertex_claude(handler: Handler) -> Upstream:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=("europe-west1",),
    )
    return VertexAnthropicAdapter(
        transport, [VertexModel("europe-west1", "anthropic", MODEL)], default_max_tokens=1024
    )


def _vertex_open_models(handler: Handler) -> Upstream:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=("europe-west1",),
        model_regions={MODEL: frozenset({"global"})},
    )
    return VertexOpenModelAdapter(
        transport,
        [VertexModel("global", "zai-org", MODEL)],
        publisher="zai-org",
        thinking_switch="enable_thinking",
    )


def _ai_studio(handler: Handler) -> Upstream:
    client = httpx.AsyncClient(
        base_url="https://studio.test/v1beta", transport=httpx.MockTransport(handler)
    )
    return GeminiUpstream("key", [MODEL], client)


def _openai(handler: Handler) -> Upstream:
    client = httpx.AsyncClient(base_url="http://local.test", transport=httpx.MockTransport(handler))
    return OpenAIAdapter(OpenAITransport(client=client), [MODEL])


#: Every platform, by the dialect it speaks: Gemini on two transports, OpenAI on two — its own and
#: Vertex's, for the managed open models.
PLATFORMS = {
    "vertex": _vertex_gemini,
    "ai-studio": _ai_studio,
    "openai": _openai,
    "vertex-open-models": _vertex_open_models,
}


def _request() -> CanonicalRequest:
    return CanonicalRequest(model=MODEL, messages=[CanonicalMessage(role=Role.USER, text="hi")])


def _sse(body: bytes) -> Handler:
    return lambda request: httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    )


def _registry(primary: Upstream) -> ProviderRegistry:
    return ProviderRegistry([primary, MockProvider(BACKUP)])


async def _text(chunks: AsyncIterator[Any]) -> str:
    return "".join([chunk.text_delta async for chunk in chunks])


# == an error inside the 200 ======================================================================

#: The first thing each dialect streams, and the error it then sends inside the same 200.
IN_BAND = {
    "anthropic": (
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":900,"output_tokens":1}}}\n\n'
        b'event: error\ndata: {"type":"error","error":'
        b'{"type":"overloaded_error","message":"Overloaded"}}\n\n'
    ),
    "gemini": (
        b'data: {"error":{"code":503,"message":"The model is overloaded.",'
        b'"status":"UNAVAILABLE"}}\n\n'
    ),
    "openai": b'data: {"error":{"message":"CUDA out of memory","type":"server_error"}}\n\n',
}


@pytest.mark.parametrize(
    ("build", "body"),
    [
        (_vertex_claude, IN_BAND["anthropic"]),
        (_vertex_gemini, IN_BAND["gemini"]),
        (_ai_studio, IN_BAND["gemini"]),
        (_openai, IN_BAND["openai"]),
        (_vertex_open_models, IN_BAND["openai"]),
    ],
    ids=["vertex-claude", "vertex-gemini", "ai-studio", "openai", "vertex-open-models"],
)
async def test_an_error_before_the_first_chunk_moves_the_chain_on(
    build: Callable[[Handler], Upstream], body: bytes
) -> None:
    """**The case the chain exists for**: an overloaded model. Read as an empty stream, the chain
    took "no chunk" for the vendor's answer and stopped — the caller got an empty `200` and the
    configured fallback was never asked."""
    streamed = await stream_with_fallback(_registry(build(_sse(body))), _request(), (BACKUP,))

    assert streamed.model == BACKUP
    assert [entry.model for entry in streamed.failed] == [MODEL]
    assert "mid-stream" in streamed.failed[0].reason
    assert await _text(streamed.chunks)


@pytest.mark.parametrize(
    ("build", "body"),
    [
        (
            _vertex_gemini,
            b'data: {"candidates":[{"content":{"parts":[{"text":"Partial ans"}]}}]}\n\n'
            + IN_BAND["gemini"],
        ),
        (
            _openai,
            b'data: {"choices":[{"delta":{"content":"Partial ans"}}]}\n\n' + IN_BAND["openai"],
        ),
        (
            _vertex_open_models,
            b'data: {"choices":[{"delta":{"content":"Partial ans"}}]}\n\n' + IN_BAND["openai"],
        ),
    ],
    ids=["gemini", "openai", "vertex-open-models"],
)
async def test_an_error_after_the_first_chunk_ends_the_stream_as_a_failure(
    build: Callable[[Handler], Upstream], body: bytes
) -> None:
    """Past the first chunk the answer cannot move to another model — but it must not look
    finished either. Half an answer with no finish reason was returned as a complete one."""
    streamed = await stream_with_fallback(_registry(build(_sse(body))), _request(), (BACKUP,))

    assert streamed.model == MODEL
    with pytest.raises(UpstreamError, match="mid-stream"):
        await _text(streamed.chunks)


# == a connection that breaks, a body that is not JSON ============================================


class _Stalls(httpx.AsyncByteStream):
    """A response whose body never arrives: the read times out."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.ReadTimeout("timed out")
        yield b""  # pragma: no cover — what makes this an async generator


class _Garbled(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"data: {not json\n\n"


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
@pytest.mark.parametrize("stream", [_Stalls, _Garbled], ids=["read-timeout", "malformed-line"])
async def test_a_stream_that_breaks_before_its_first_chunk_moves_the_chain_on(
    platform: str, stream: type[httpx.AsyncByteStream]
) -> None:
    """A cold model that outlasts the read timeout, a proxy that mangles a line: the upstream's
    failure. It escaped as `httpx.ReadTimeout` or `JSONDecodeError` — a 500 blaming the gateway,
    the fallback never tried — on every transport but one."""
    primary = PLATFORMS[platform](lambda request: httpx.Response(200, stream=stream()))

    streamed = await stream_with_fallback(_registry(primary), _request(), (BACKUP,))

    assert streamed.model == BACKUP
    assert [entry.model for entry in streamed.failed] == [MODEL]


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
async def test_a_200_that_is_not_json_moves_the_chain_on(platform: str) -> None:
    """A corporate proxy's maintenance page answers `200` with HTML. Parsed as an answer, it was a
    `JSONDecodeError` — a 500 that blamed the gateway, the fallback never tried."""
    primary = PLATFORMS[platform](lambda request: httpx.Response(200, text="<html>down</html>"))

    dispatched = await dispatch_with_fallback(_registry(primary), _request(), (BACKUP,))

    assert dispatched.response.model == BACKUP
    assert [entry.model for entry in dispatched.failed] == [MODEL]
    assert "not JSON" in dispatched.failed[0].reason


# == a refusal says why, on every transport, streamed or not ======================================


class _Unread(httpx.AsyncByteStream):
    """A refusal's body that has genuinely not been read yet, as a real streamed refusal is."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"error": {"message": "mimeType application/x-javascript is not supported"}}'


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
@pytest.mark.parametrize("streamed", [False, True], ids=["generate", "stream"])
async def test_a_400_carries_the_reason_the_provider_gave(platform: str, streamed: bool) -> None:
    """`FRD-129`: a 400 names a fault in the body *we* built, and its reason is the most actionable
    thing anybody gets. It reached the caller on one transport both ways, on one only when not
    streamed — a streamed refusal was judged before its body was read — and on one never."""
    adapter = PLATFORMS[platform](lambda request: httpx.Response(400, stream=_Unread()))

    with pytest.raises(UpstreamError) as caught:
        if streamed:
            await _text(adapter.stream_generate(_request()))
        else:
            await adapter.generate(_request())

    assert caught.value.status_code == 400
    assert "application/x-javascript" in caught.value.message


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
async def test_a_refused_stream_is_closed_before_the_refusal_is_raised(platform: str) -> None:
    """Under regional failover and the chain a refused stream is an ordinary step; one left open
    holds its connection out of the pool until the garbage collector gets to it."""
    closed: list[bool] = []

    class _Tracked(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"error": {"message": "no"}}'

        async def aclose(self) -> None:
            closed.append(True)

    adapter = PLATFORMS[platform](lambda request: httpx.Response(429, stream=_Tracked()))

    with pytest.raises(UpstreamError):
        await _text(adapter.stream_generate(_request()))

    assert closed == [True]


# == through the surface: the row says the upstream failed ========================================


class _Pipeline:
    """No pipeline: the request reaches the chain exactly as sent."""

    async def get(self, use_case: str | None) -> Any:
        from aira_gateway.pipeline.config import Pipeline

        return Pipeline()


async def test_a_stream_that_failed_mid_answer_is_recorded_as_the_upstreams_failure() -> None:
    """**The audit row is the evidence.** The stream broke off after its first words, the caller
    already had a `200`, and the row said `499 / client_gone` — the caller blamed for the provider
    failing, and nothing settled. It says the upstream failed."""
    server = _openai(
        _sse(
            b'data: {"choices":[{"delta":{"content":"The answer is"}}]}\n\n'
            b'data: {"error":{"message":"CUDA out of memory","type":"server_error"}}\n\n'
        )
    )
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    registry = ProviderRegistry([server])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Pipeline()

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model=MODEL, approved=True, capabilities=["generate"]))
            await session.commit()
        response = client.post(
            f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 200, "the status had gone out with the first chunk"
    assert "The answer is" in response.text
    assert [row.outcome for row in rows] == ["upstream_error"]
    assert rows[0].status != 499


# == one reader for every stream ==================================================================


async def test_the_stream_reader_takes_the_events_and_nothing_else() -> None:
    """Keep-alives, comments, `event:` names and OpenAI's `[DONE]` carry nothing a dialect reads —
    every vendor repeats the event type inside the payload — and a `data:` line is read with or
    without the space after the colon, as the SSE format allows."""
    from aira_gateway.upstreams.exchange import sse_payloads

    body = (
        b": keep-alive\n\n"
        b"event: ping\n\n"
        b"data:\n\n"
        b'data:{"a": 1}\n\n'
        b'data: {"b": 2}\n\n'
        b'data: ["not", "an", "object"]\n\n'
        b"data: [DONE]\n\n"
    )
    response = httpx.Response(200, content=body)

    assert [payload async for payload in sse_payloads(response, "Upstream")] == [{"a": 1}, {"b": 2}]


async def test_connecting_is_bounded_apart_from_answering() -> None:
    """A model may think for minutes before its first byte, so the read allowance is long; a host
    that accepts no connection within seconds is down. One scalar made the connect allowance the
    read allowance — two minutes, five on a self-hosted server — so the chain waited out the whole
    of it before trying the next model: slowest for exactly the outage it exists for."""
    from aira_gateway.upstreams.exchange import CONNECT_TIMEOUT_SECONDS

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout") or {})
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    client = httpx.AsyncClient(base_url="http://local.test", transport=httpx.MockTransport(handler))
    adapter = OpenAIAdapter(OpenAITransport(client=client, timeout=300.0), [MODEL])

    await adapter.generate(_request())

    assert seen[0]["connect"] == CONNECT_TIMEOUT_SECONDS
    assert seen[0]["read"] == 300.0


def test_the_vertex_client_is_built_with_a_short_connect_allowance() -> None:
    from aira_gateway.upstreams.exchange import CONNECT_TIMEOUT_SECONDS
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    upstreams = build_vertex_upstreams(
        GatewaySettings(
            vertex_project="p",
            vertex_api_key="AQ.key",
            allowed_regions="europe-west1",
            vertex_timeout_seconds=120.0,
        )
    )

    client = upstreams[0]._transport._client  # type: ignore[attr-defined]
    assert client.timeout.connect == CONNECT_TIMEOUT_SECONDS
    assert client.timeout.read == 120.0
