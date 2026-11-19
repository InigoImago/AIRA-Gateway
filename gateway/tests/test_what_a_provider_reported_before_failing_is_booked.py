"""What a provider reported using before a call failed is booked: the vendor bills it.

**What this is about.** Claude on Vertex is sent every request as a stream, the ones the caller
did not stream included, and states the input it charges in its first event, `message_start` —
for an agent turn on a large repository, hundreds of thousands of tokens. When the stream then
broke off — the connection dropped, this gateway's time limit, an `overloaded_error` mid-answer —
the streamed verb booked what Claude had reported as the floor it is (`partial`). The same answer
read for a caller who did not stream — `:generateContent`, the KIRA chat — booked nothing: no
tokens, no cost, and the budget never saw it. Where the next region then served, the row carried
the second region's usage, and the first region's input was paid and not booked. The failure now
carries what was reported (`UpstreamError.spent`), and the region that served carries what the
regions before it reported.

The same shape on the embedding side: a Vertex model that embeds one text per call fails a batch
at its tenth text with nine calls already billed, and the region that then serves the whole batch
— or the failure, where none does — carried none of them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    Role,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter
from aira_gateway.upstreams.vertex.regions import VertexModel
from aira_gateway.upstreams.vertex.transport import VertexTransport

MODEL = "claude-sonnet-4-5@20250929"
REGIONS = ["europe-west1", "europe-west4"]

#: Per million tokens, in nano-units: input 1 000, output 2 000 per token.
_PRICES = {
    "input_price_per_million_nanos": 1_000_000_000,
    "output_price_per_million_nanos": 2_000_000_000,
}

_START = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"m",'
    b'"type":"message","role":"assistant","content":[],'
    b'"usage":{"input_tokens":900,"output_tokens":1}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Par"}}\n\n'
)
_REST = (
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":4}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


_OVERLOADED = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error"}}\n\n'


def _elsewhere() -> CanonicalRequest:
    """A Claude model the catalogue places in two regions, in order."""
    return CanonicalRequest(
        model="claude-elsewhere",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        addressing={"regions": REGIONS},
    )


class _BreaksAfterTheFirstWord(httpx.AsyncByteStream):
    """Claude's answer as far as `message_start` and a first word — then the connection drops."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _START
        raise httpx.RemoteProtocolError("peer closed connection without sending complete body")


def _claude(handler: Any) -> VertexAnthropicAdapter:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=("eu", *REGIONS),
    )
    return VertexAnthropicAdapter(
        transport, [VertexModel("eu", "anthropic", MODEL)], default_max_tokens=1024
    )


def _breaks(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, stream=_BreaksAfterTheFirstWord(), headers={"content-type": "text/event-stream"}
    )


def _rows(adapter: Any, path: str, body: dict[str, Any]) -> list[tuple[Any, ...]]:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([adapter])
    with TestClient(app) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model=MODEL, numeric_id=1, capabilities=["generate", "embed"], **_PRICES
                    )
                )
                session.add(UseCaseRead(slug="demo", name="demo"))
                await session.commit()

        client.portal.call(seed)  # type: ignore[union-attr]
        client.post(path, json=body, headers={"X-AIRA-Use-Case": "demo"})

        async def read() -> list[tuple[Any, ...]]:
            async with app.state.db_sessionmaker() as session:
                found = (await session.execute(select(RequestLog))).scalars()
                return [
                    (row.outcome, row.prompt_tokens, row.usage_basis, row.cost_nanos)
                    for row in found
                ]

        return client.portal.call(read)  # type: ignore[union-attr]


GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

#: Each verb that reads Claude for a caller who did not stream, and the streamed one beside them.
VERBS = {
    "streamed": (f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse", GEMINI_BODY),
    "generateContent": (f"/v1beta/models/{MODEL}:generateContent", GEMINI_BODY),
    "kira-chat": (
        "/kira/api/external/chat",
        {"request": {"parts": [{"text": "hi"}]}, "model_id": 1},
    ),
}


@pytest.mark.parametrize("verb", VERBS)
def test_a_claude_answer_that_broke_off_books_what_claude_reported(verb: str) -> None:
    """The row of the failed request carries the 900 input tokens Claude reported, marked as the
    floor they are, and their cost — whichever verb read the answer."""
    path, body = VERBS[verb]

    rows = _rows(_claude(_breaks), path, body)

    assert len(rows) == 1
    outcome, prompt_tokens, basis, cost = rows[0]
    assert (outcome, prompt_tokens, basis) == ("upstream_error", 900, "partial")
    assert cost is not None and cost > 0


async def test_the_region_tried_first_is_booked_with_the_one_that_served() -> None:
    """Both were paid: the first for the input it read before it broke off, the second for the
    whole answer. One model at one price, so the answer carries both."""

    def handler(request: httpx.Request) -> httpx.Response:
        first = request.url.host.startswith(REGIONS[0])
        return httpx.Response(200, content=_START + (_OVERLOADED if first else _REST))

    answer = await _claude(handler).generate(_elsewhere())

    assert (answer.text, answer.served_region) == ("Par", REGIONS[1])
    assert answer.usage.prompt_tokens == 1800, "the first region's input was paid and not booked"
    assert answer.usage.completion_tokens == 4 + 1


async def test_a_failure_in_every_region_carries_what_each_reported() -> None:
    """Overloaded in both: each read the input before it broke off, and the failure that reaches
    the caller's row carries both."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_START + _OVERLOADED)

    with pytest.raises(UpstreamError) as caught:
        await _claude(handler).generate(_elsewhere())

    assert caught.value.spent is not None
    assert caught.value.spent.prompt_tokens == 900 + 900


# == an embedding batch sent one text per call ====================================================


def _predict(token_count: int) -> dict[str, Any]:
    return {
        "predictions": [
            {"embeddings": {"values": [0.1, 0.2], "statistics": {"token_count": token_count}}}
        ]
    }


def _embedder(handler: Any) -> VertexGeminiAdapter:
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=tuple(REGIONS),
    )
    return VertexGeminiAdapter(transport, [])


def _batch() -> CanonicalEmbeddingRequest:
    return CanonicalEmbeddingRequest(
        model="text-embedding-005",
        texts=["one", "two", "three"],
        addressing={"regions": REGIONS},
    )


async def test_the_texts_a_failed_region_embedded_are_booked_with_the_batch() -> None:
    """The first region embeds one text and runs out of quota at the second; the next region
    embeds all three. Four calls were billed, and the vectors say so."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        region = request.url.host.split("-aiplatform")[0]
        calls.append(region)
        if region == REGIONS[0] and calls.count(region) > 1:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return httpx.Response(200, json=_predict(5))

    vectors = await _embedder(handler).embed(_batch())

    assert calls == [REGIONS[0], REGIONS[0], REGIONS[1], REGIONS[1], REGIONS[1]]
    assert vectors.input_tokens == 5 + 3 * 5  # type: ignore[attr-defined]


async def test_a_batch_no_region_finished_carries_what_was_embedded() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        region = request.url.host.split("-aiplatform")[0]
        calls.append(region)
        if calls.count(region) > 1:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return httpx.Response(200, json=_predict(5))

    with pytest.raises(UpstreamError) as caught:
        await _embedder(handler).embed(_batch())

    assert caught.value.status_code == 429
    assert caught.value.spent is not None
    assert caught.value.spent.prompt_tokens == 5 + 5
