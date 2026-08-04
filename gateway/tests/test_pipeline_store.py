from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import PipelineConfigRead
from aira_gateway.pipeline.store import PipelineStore


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_get_none_for_missing_or_unset(sessionmaker) -> None:
    store = PipelineStore(sessionmaker)
    assert await store.get(None) is None
    assert await store.get("") is None
    assert await store.get("unknown") is None


async def test_get_returns_configured_pipeline(sessionmaker) -> None:
    async with sessionmaker() as session:
        session.add(
            PipelineConfigRead(
                use_case="demo-uc",
                steps=[{"type": "injection_filter", "config": {"mode": "heuristic"}}],
                fallback_models=["backup-1"],
            )
        )
        await session.commit()

    pipeline = await PipelineStore(sessionmaker).get("demo-uc")
    assert pipeline is not None
    assert pipeline.fallback_models == ("backup-1",)
    assert len(pipeline.steps) == 1


async def test_empty_config_is_treated_as_none(sessionmaker) -> None:
    async with sessionmaker() as session:
        session.add(PipelineConfigRead(use_case="empty-uc", steps=[], fallback_models=[]))
        await session.commit()
    assert await PipelineStore(sessionmaker).get("empty-uc") is None
