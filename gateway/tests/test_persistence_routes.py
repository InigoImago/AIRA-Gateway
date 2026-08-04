from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import create_all
from aira_gateway.db.models import RequestLog

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi there friend"}]}]}


async def _rows(app) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars().all())


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    app = create_app(GatewaySettings(log_json=True, auth_required=False))
    await create_all(app.state.db_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client
    await app.state.db_engine.dispose()


async def test_generate_is_persisted(app_client) -> None:
    app, client = app_client
    resp = await client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 200

    rows = await _rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "generateContent"
    assert row.model == "mock-1"
    assert row.subject == "demo"
    assert row.status == 200
    assert row.total_tokens == row.prompt_tokens + row.completion_tokens
    assert row.latency_ms is not None
    assert row.request_payload is not None
    assert row.response_payload is not None


async def test_embed_is_persisted(app_client) -> None:
    app, client = app_client
    resp = await client.post(
        "/v1beta/models/mock-1:embedContent", json={"content": {"parts": [{"text": "x"}]}}
    )
    assert resp.status_code == 200
    row = (await _rows(app))[0]
    assert row.operation == "embedContent"
    assert row.prompt_tokens is None


async def test_stream_is_persisted(app_client) -> None:
    app, client = app_client
    resp = await client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
    assert resp.status_code == 200
    assert resp.text  # drain the stream so the recorder runs
    row = (await _rows(app))[0]
    assert row.operation == "streamGenerateContent"
    assert row.response_payload is not None
    assert "text" in row.response_payload


async def test_store_payloads_disabled_keeps_metadata_only() -> None:
    app = create_app(GatewaySettings(auth_required=False, store_payloads=False))
    await create_all(app.state.db_engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
        assert resp.status_code == 200
    row = (await _rows(app))[0]
    assert row.request_payload is None
    assert row.response_payload is None
    assert row.total_tokens is not None  # metadata still recorded
    await app.state.db_engine.dispose()
