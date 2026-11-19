"""A streamed request answered `200` with something that is not an event stream has failed.

**What this is about.** A corporate proxy's block page, a web application behind a wrong base URL,
a server that ignored `stream: true` and sent its whole answer as one JSON object: each is a `200`
holding no `data:` event at all. The stream reader skips every line that is not an event, so the
adapter produced no chunk — and a stream with no chunk is the vendor's own answer to the chain,
which stopped: the caller got an empty `200`, the configured fallback was never asked, and the row
said `served`, with no usage and no cost. The same body unstreamed was always a 502 the chain moved
on for.

Every vendor's stream says something, its refusals included, so a body with no event in it is no
answer — and through the real adapters, which is where the difference lies: the chain's own test
of "a stream with no chunk has answered" uses a stand-in that yields nothing, which is more
permissive than any adapter here.
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
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.dispatch import stream_with_fallback
from aira_gateway.upstreams.base import ProviderRegistry, Upstream
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
MODEL = "primary-1"
BACKUP = "backup-1"


def _vertex(handler: Handler, **extra: Any) -> VertexTransport:
    return VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=("europe-west1",),
        **extra,
    )


PLATFORMS: dict[str, Callable[[Handler], Upstream]] = {
    "vertex-gemini": lambda h: VertexGeminiAdapter(
        _vertex(h), [VertexModel("europe-west1", "google", MODEL)]
    ),
    "vertex-claude": lambda h: VertexAnthropicAdapter(
        _vertex(h), [VertexModel("europe-west1", "anthropic", MODEL)], default_max_tokens=1024
    ),
    "vertex-open-models": lambda h: VertexOpenModelAdapter(
        _vertex(h, model_regions={MODEL: frozenset({"global"})}),
        [VertexModel("global", "zai-org", MODEL)],
        publisher="zai-org",
        thinking_switch="enable_thinking",
    ),
    "ai-studio": lambda h: GeminiUpstream(
        "key",
        [MODEL],
        httpx.AsyncClient(base_url="https://studio.test/v1beta", transport=httpx.MockTransport(h)),
    ),
    "openai": lambda h: OpenAIAdapter(
        OpenAITransport(
            client=httpx.AsyncClient(base_url="http://local.test", transport=httpx.MockTransport(h))
        ),
        [MODEL],
    ),
}

#: What a corporate proxy, a captive portal or a misrouted endpoint answers with a `200`.
BODIES: dict[str, Callable[[], httpx.Response]] = {
    "html-block-page": lambda: httpx.Response(
        200,
        text="<html><body>Access to this site is blocked.</body></html>",
        headers={"content-type": "text/html"},
    ),
    "json-not-a-stream": lambda: httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "the whole answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    ),
    "keepalives-only": lambda: httpx.Response(
        200, content=b": ping\n\nevent: ping\n\n", headers={"content-type": "text/event-stream"}
    ),
}


def _request() -> CanonicalRequest:
    return CanonicalRequest(model=MODEL, messages=[CanonicalMessage(role=Role.USER, text="hi")])


async def _text(chunks: AsyncIterator[Any]) -> str:
    return "".join([chunk.text_delta async for chunk in chunks])


@pytest.mark.parametrize("body", sorted(BODIES))
@pytest.mark.parametrize("platform", sorted(PLATFORMS))
async def test_a_streamed_200_that_is_no_event_stream_moves_the_chain_on(
    platform: str, body: str
) -> None:
    primary = PLATFORMS[platform](lambda request: BODIES[body]())
    registry = ProviderRegistry([primary, MockProvider(BACKUP)])

    streamed = await stream_with_fallback(registry, _request(), (BACKUP,))

    assert streamed.model == BACKUP, f"served by {streamed.model!r}"
    assert [entry.model for entry in streamed.failed] == [MODEL]
    assert "not an event stream" in streamed.failed[0].reason
    assert (await _text(streamed.chunks)).strip()


def test_the_row_of_such_a_stream_says_the_upstream_failed() -> None:
    """End to end on the Gemini surface, with no fallback configured: the caller is told the
    upstream failed, and the row says so — not `served`, free, with no usage."""
    adapter = PLATFORMS["openai"](lambda request: BODIES["html-block-page"]())
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([adapter])
    with TestClient(app) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(ModelRead(model=MODEL, numeric_id=1, capabilities=["generate"]))
                session.add(UseCaseRead(slug="demo", name="demo"))
                await session.commit()

        client.portal.call(seed)  # type: ignore[union-attr]
        response = client.post(
            f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            headers={"X-AIRA-Use-Case": "demo"},
        )

        async def read() -> list[tuple[Any, ...]]:
            async with app.state.db_sessionmaker() as session:
                found = (await session.execute(select(RequestLog))).scalars()
                return [(row.status, row.outcome) for row in found]

        rows = client.portal.call(read)  # type: ignore[union-attr]

    assert response.status_code == 502, response.text
    assert rows == [(502, "upstream_error")]
