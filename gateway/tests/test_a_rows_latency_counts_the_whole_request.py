"""A row's latency counts the whole request, on either surface.

`latency_ms` is what the reports average and the request list sorts by, and the same request sent
to either surface is compared by it. The KIRA layer measures from the moment its route begins, so
its rows count the whole way to dispatch: parsing, the use case, the rate limit, the pipeline and
the model calls its steps make, the declaration, the reservation. The Gemini surface started a fresh
clock for its served rows once that was done, and left it out — while its refusals, measured from
the route's start like KIRA's, counted it. A classifier step's model call took hundreds of
milliseconds off every Gemini row and none off a KIRA one.

Here the way to dispatch is made to take 300 ms, as a classifier step's model call does: every row
shows at least that, whichever surface and verb produced it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from aira_gateway.api.serving import prepare
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalChunk, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

#: How long the pipeline takes here: a classifier step's model call.
PIPELINE_SECONDS = 0.3

GEMINI = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
KIRA = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}

#: Every verb of both surfaces that reaches a model.
REQUESTS = [
    ("/v1beta/models/m-1:generateContent", GEMINI),
    ("/v1beta/models/m-1:streamGenerateContent?alt=sse", GEMINI),
    ("/v1beta/models/m-1:embedContent", {"content": {"parts": [{"text": "hi"}]}}),
    ("/kira/api/external/chat", KIRA),
    ("/kira/api/external/streaming-chat", KIRA),
    ("/kira/api/external/embed", {"text": "hi", "model_id": 1}),
]


class _Upstream:
    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent", "embedContent")
        return [UpstreamModel("m-1", "1", verbs, provider="t", region="eu")]

    async def generate(self, request: Any) -> CanonicalResponse:
        return CanonicalResponse(
            model="m-1", text="hello", usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2)
        )

    async def stream_generate(self, request: Any) -> Any:
        yield CanonicalChunk(
            text_delta="hello",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2),
            finish_reason="stop",
        )

    async def embed(self, request: Any) -> list[list[float]]:
        return [[0.0, 1.0]]


async def test_a_served_row_counts_the_way_to_dispatch_on_both_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for step in ("run_pipeline", "run_pipeline_over_texts"):
        original = getattr(prepare, step)

        async def a_classifier_step_that_calls_a_model(*args: Any, _step: Any = original) -> Any:
            await asyncio.sleep(PIPELINE_SECONDS)
            return await _step(*args)

        monkeypatch.setattr(prepare, step, a_classifier_step_that_calls_a_model)

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([_Upstream()])
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="m-1", numeric_id=1, capabilities=["generate", "embed"]))
            session.add(UseCaseRead(slug="demo", name="demo"))
            await session.commit()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path, body in REQUESTS:
                answered = await client.post(path, json=body, headers={"x-aira-use-case": "demo"})
                assert answered.status_code == 200, (path, answered.text)
        async with app.state.db_sessionmaker() as session:
            rows = {
                (row.api, row.operation): row.latency_ms
                for row in (await session.execute(select(RequestLog))).scalars()
            }

    assert len(rows) == len(REQUESTS)
    floor = PIPELINE_SECONDS * 1000
    assert {where: ms for where, ms in rows.items() if ms < floor} == {}, (
        f"rows that left out the {floor:.0f} ms before dispatch: {rows}"
    )
