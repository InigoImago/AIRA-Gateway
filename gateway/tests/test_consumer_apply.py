from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_common.kafka import EVENT_TYPE_HEADER
from aira_gateway.consumer.apply import apply_event
from aira_gateway.consumer.worker import decode_event_type
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import UseCaseMemberRead, UseCaseRead


@pytest_asyncio.fixture
async def make_session() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _all(sessionmaker, model):
    async with sessionmaker() as session:
        return list((await session.execute(select(model))).scalars().all())


async def test_usecase_upsert_is_idempotent(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N2"})
    rows = await _all(make_session, UseCaseRead)
    assert len(rows) == 1
    assert rows[0].name == "N2"


async def test_usecase_delete_removes_members(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(
            session, "membership.upserted", {"slug": "uc", "username": "alice", "role": "admin"}
        )
        await apply_event(session, "usecase.deleted", {"slug": "uc"})
    assert await _all(make_session, UseCaseRead) == []
    assert await _all(make_session, UseCaseMemberRead) == []


async def test_membership_upsert_updates_role_then_remove(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "N"})
        await apply_event(session, "membership.upserted", {"slug": "uc", "username": "alice"})
        await apply_event(
            session, "membership.upserted", {"slug": "uc", "username": "alice", "role": "admin"}
        )
    members = await _all(make_session, UseCaseMemberRead)
    assert len(members) == 1
    assert members[0].role == "admin"

    async with make_session() as session:
        await apply_event(session, "membership.removed", {"slug": "uc", "username": "alice"})
    assert await _all(make_session, UseCaseMemberRead) == []


async def test_unknown_event_is_ignored(make_session) -> None:
    async with make_session() as session:
        await apply_event(session, "something.else", {"x": 1})
    assert await _all(make_session, UseCaseRead) == []


def test_decode_event_type() -> None:
    assert decode_event_type([(EVENT_TYPE_HEADER, b"usecase.upserted")]) == "usecase.upserted"
    assert decode_event_type([("other", b"x")]) is None
    assert decode_event_type(None) is None
