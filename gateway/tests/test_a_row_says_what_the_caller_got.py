"""What a request's row says is what its caller got.

Every verb booked a request as served — settled, its row written with `200` / `served` — and only
then built the response it answered with. Building it can fail: Starlette writes the JSON body
there, refusing a value JSON has no word for, and encodes the headers, refusing what Latin-1 cannot
carry. Both reach that point from outside: a vector or a score holding `NaN` from an upstream —
Python's JSON parser reads `NaN`, and a Python-built server writes it — and a deprecated model whose
catalogue name is not Latin-1, which Management accepts, named in the `Warning` header. The caller
got a `500` while the row said served and the booking charged for it, so a client that retried paid
twice. A stream built its response after its chain had opened and before anything accounted for it,
so the same header left no row at all and an upstream stream nobody closed.

So every verb builds its response before the request counts as served, and a failure there is
recorded as the `500` / `internal_error` it is — with what the vendor billed booked all the same —
and the `Warning` header is written so that any name can travel in it.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    RankedRecord,
    RankedRecords,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai.mapping.response import embedding_values

#: An OpenAI-compatible embedding answer as a Python-built server writes a degenerate vector
#: (`json.dumps` writes `NaN` by default), parsed as the adapter's transport parses it.
NAN_ANSWER = httpx.Response(
    200, content=b'{"data":[{"index":0,"embedding":[NaN,0.25]}],"usage":{"prompt_tokens":2}}'
).json()

#: A deprecated model under a name Management accepts and Latin-1 cannot carry (an en dash).
DASHED = "slow\u20131"

GEMINI = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _Upstream:
    """A model that answers every verb — its vectors as the degenerate answer above."""

    is_test_double = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed: list[bool] = []

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent", "embedContent")
        return [UpstreamModel(self.name, "1", verbs, provider="t", region="eu")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=self.name,
            text="hello",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        try:
            yield CanonicalChunk(text_delta="hel")
            await asyncio.sleep(0)
            yield CanonicalChunk(
                text_delta="lo",
                usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2),
                finish_reason="stop",
            )
        finally:
            self.closed.append(True)

    async def embed(self, request: object) -> list[list[float]]:
        return embedding_values(NAN_ANSWER)


def _app(provider: _Upstream) -> Any:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])
    return app


async def _seed(app: Any, model: ModelRead) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(model)
        session.add(UseCaseRead(slug="demo", name="demo"))
        await session.commit()


async def _rows(app: Any) -> list[tuple[Any, ...]]:
    async with app.state.db_sessionmaker() as session:
        return [
            (row.operation, row.status, row.outcome, row.prompt_tokens)
            for row in (await session.execute(select(RequestLog))).scalars()
        ]


@pytest.mark.parametrize(
    ("path", "body", "operation"),
    [
        (
            "/v1beta/models/emb-1:embedContent",
            {"content": {"parts": [{"text": "hi"}]}},
            "embedContent",
        ),
        ("/kira/api/external/embed", {"text": "hi", "model_id": 1}, "embed"),
    ],
    ids=["gemini", "kira"],
)
async def test_an_embedding_that_cannot_be_written_is_recorded_as_the_failure_it_was(
    path: str, body: dict[str, Any], operation: str
) -> None:
    """The vector cannot be written as JSON, so the caller gets a `500` — and the row says so,
    with the tokens the vendor billed for it."""
    assert math.isnan(NAN_ANSWER["data"][0]["embedding"][0]), "the transport's parse keeps NaN"
    app = _app(_Upstream("emb-1"))
    with TestClient(app, raise_server_exceptions=False) as client:
        await _seed(app, ModelRead(model="emb-1", numeric_id=1, capabilities=["embed"]))
        response = client.post(path, json=body, headers={"x-aira-use-case": "demo"})
        rows = await _rows(app)

    assert response.status_code == 500
    assert rows == [(operation, 500, "internal_error", 2)]


async def _scores_nothing(self: MockProvider, request: Any) -> RankedRecords:
    return RankedRecords([RankedRecord(id=record.id, score=math.nan) for record in request.records])


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/kira/api/external/rerank",
            {
                "model_id": 3001,
                "query": "q",
                "records": [{"id": "a", "title": "t", "content": "c"}],
            },
        ),
        (
            "/v1/projects/p/locations/global/rankingConfigs/default_ranking_config:rank",
            {"model": "mock-1", "query": "q", "records": [{"id": "a", "title": "t"}]},
        ),
    ],
    ids=["kira", "google"],
)
async def test_a_ranking_that_cannot_be_written_is_recorded_as_the_failure_it_was(
    path: str, body: dict[str, Any]
) -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with (
        mock.patch.object(MockProvider, "rerank", _scores_nothing),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        await _seed(
            app, ModelRead(model="mock-1", numeric_id=3001, capabilities=["rerank"], approved=True)
        )
        response = client.post(path, json=body, headers={"x-aira-use-case": "demo"})
        rows = await _rows(app)

    assert response.status_code == 500
    assert rows == [("rerank", 500, "internal_error", 1)]


async def test_a_deprecated_model_under_any_name_is_answered_and_recorded_as_served() -> None:
    """The `Warning` header names the model in what Latin-1 can carry, so the answer goes out — on
    the streamed verb too, which then closes the upstream stream it opened."""
    provider = _Upstream(DASHED)
    app = _app(provider)
    with TestClient(app) as client:
        await _seed(app, ModelRead(model=DASHED, capabilities=["generate"], deprecated=True))
        headers = {"x-aira-use-case": "demo"}
        answered = client.post(
            f"/v1beta/models/{DASHED}:generateContent", json=GEMINI, headers=headers
        )
        streamed = client.post(
            f"/v1beta/models/{DASHED}:streamGenerateContent?alt=sse", json=GEMINI, headers=headers
        )
        rows = await _rows(app)

    assert answered.status_code == streamed.status_code == 200
    assert answered.headers["warning"] == streamed.headers["warning"]
    assert answered.headers["warning"].startswith('299 - "Model slow')
    assert sorted(rows) == [
        ("generateContent", 200, "served", 3),
        ("streamGenerateContent", 200, "served", 3),
    ]
    assert provider.closed == [True]
