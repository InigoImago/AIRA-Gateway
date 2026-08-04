from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.auth import keys
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


@pytest_asyncio.fixture
async def make_session() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_create_and_verify(make_session) -> None:
    async with make_session() as session:
        full, record = await ApiKeyService(session).create("user-1", "my key")
        assert record.subject == "user-1"
        assert record.is_active is True

    async with make_session() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.subject == "user-1"
    assert principal.method == "api_key"
    assert principal.label == "my key"


async def test_verify_unknown_or_malformed(make_session) -> None:
    async with make_session() as session:
        service = ApiKeyService(session)
        assert await service.verify("aira_dead_beef") is None
        assert await service.verify("notakey") is None


async def test_revoke(make_session) -> None:
    async with make_session() as session:
        service = ApiKeyService(session)
        full, record = await service.create("u")
        assert await service.revoke(record.prefix) is True

    async with make_session() as session:
        assert await ApiKeyService(session).verify(full) is None

    async with make_session() as session:
        assert await ApiKeyService(session).revoke("nope") is False


async def test_ensure_demo_key_idempotent(make_session) -> None:
    async with make_session() as session:
        service = ApiKeyService(session)
        await service.ensure_demo_key()
        await service.ensure_demo_key()

    async with make_session() as session:
        principal = await ApiKeyService(session).verify(keys.DEMO_API_KEY)
    assert principal is not None
    assert principal.subject == "demo"
