"""A redactor's calls over a batch weigh on a request budget as the calls they are (`FRD-125`).

A pipeline step's call reaches a model and costs money, so a request budget counts each one as a
request (`FRD-125` FR-9b), as it counts each text of an embedding batch the caller sends
(`FRD-113` FR-6). Over a batch the redactor makes one call per text and the step keeps one row for
them all (`FRD-309` FR-11) — and that row was booked as **one** request. Sending the same texts one
per request booked one per text; sending them together booked one for all, so batching was a way
past the request budget for exactly the calls the use case's own pipeline made.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis.aioredis as fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.app import create_app
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.ledger import Amounts, BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.base import Base
from aira_gateway.db.models import BudgetRead, BudgetUsage, ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

TEXTS = [
    "Bitte an Max Mustermann senden.",
    "Ruf Erika Beispiel an.",
    "Termin mit Hans Meier.",
]


class _Provider:
    """Redacts every text it is asked about, and embeds."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("trusted", "trusted", ("generateContent", "embedContent"))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        text = request.messages[-1].text
        return CanonicalResponse(
            model="trusted",
            text=text.split("\n", 1)[1].rsplit("\n", 1)[0],
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=5),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request) -> list[list[float]]:  # noqa: ANN001
        return [[0.5, 0.5] for _ in request.texts]


async def test_a_batch_books_a_request_for_every_text_the_redactor_was_asked_about() -> None:
    class _Store:
        async def get(self, use_case: object) -> Pipeline:
            return Pipeline(steps=(PipelineStep(StepType.PII_FILTER, {"model": "trusted"}),))

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([_Provider()])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction.
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=["trusted"]))
            session.add(
                ModelRead(
                    model="trusted",
                    approved=True,
                    capabilities=["generate", "embed"],
                    embedding={"dimensions": [2], "supports_batch": True},
                )
            )
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_requests=1000,
                    enabled=True,
                )
            )
            await session.commit()

        response = client.post(
            "/v1beta/models/trusted:batchEmbedContents",
            json={
                "requests": [
                    {"model": "models/trusted", "content": {"parts": [{"text": text}]}}
                    for text in TEXTS
                ]
            },
            headers={"x-aira-use-case": "uc"},
        )
        async with app.state.db_sessionmaker() as session:
            usage = list((await session.execute(select(BudgetUsage))).scalars())
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 200, response.text
    # Still one row for the step (`FRD-309` FR-11) …
    assert [row.operation for row in rows if row.operation.startswith("pipeline:")] == [
        "pipeline:pii_filter"
    ]
    # … standing for three calls: three texts the caller sent, three the redactor was asked about.
    assert [entry.requests for entry in usage] == [2 * len(TEXTS)]


# == the shared counter the guard reads ===========================================================


class _Counters:
    """An in-process Redis that runs the ledger's own scripts — the counter enforcement reads."""

    def __init__(self) -> None:
        self._client = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        registered = self._client.register_script(script)
        return await registered(keys=list(keys), args=list(args))


async def test_the_counter_the_guard_reads_counts_every_call_too() -> None:
    """Both stores: Postgres is what reporting reads, the shared counter is what the next request
    is refused against. A booking that moved one by three and the other by one would let the
    calls through the very limit that counts them."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                subject="",
                period="month",
                limit_requests=3,
                enabled=True,
            )
        )
        await session.commit()
    service = BudgetService(sessionmaker, ledger=BudgetLedger(_Counters()))
    # Warm the counter, as a live one is by the second request: a cold one is seeded from
    # Postgres and would show a Postgres-only booking anyway.
    warm = await service.guard("uc", "alice", now, estimated=Amounts(requests=1))
    await service.settle(warm, 1, now=now)

    await service.book_side_call("uc", "alice", 30, now=now, requests=len(TEXTS))

    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "alice", now, estimated=Amounts(requests=1))
    await engine.dispose()
