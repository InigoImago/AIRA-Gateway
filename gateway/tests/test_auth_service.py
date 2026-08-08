from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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


# ---- expiry (2026-08-08) --------------------------------------------------------------------
#
# A credential with no end date has to be *inventoried* rather than lapsing on its own. Optional
# on purpose: NULL means never, which is every key issued before this existed and what the CLI
# break-glass key needs — an expiry that cannot be omitted is one an operator sets to the year
# 3000, and then nothing has an end date and the column lies.


async def test_an_expired_key_no_longer_verifies(make_session) -> None:
    async with make_session() as session:
        service = ApiKeyService(session)
        full, record = await service.create("ada")
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        assert await service.verify(full) is None


async def test_a_key_verifies_right_up_to_its_expiry(make_session) -> None:
    async with make_session() as session:
        service = ApiKeyService(session)
        full, record = await service.create("ada")
        record.expires_at = datetime.now(UTC) + timedelta(hours=1)
        await session.commit()

        assert await service.verify(full) is not None


async def test_a_new_key_is_bounded_without_anybody_asking(make_session) -> None:
    """**A key is always bounded.** An earlier version of this file asserted the opposite — that a
    key created with no arguments carries NULL — on the argument that an expiry which cannot be
    omitted is one somebody sets to the year 3000. That argument is about the *maximum*; the
    default is what decides whether anybody has to remember, and nobody does.

    This is the **break-glass** path: a key minted by hand during an incident is precisely the one
    that outlives its reason.
    """
    async with make_session() as session:
        service = ApiKeyService(session)
        full, record = await service.create("ada")

        assert record.expires_at is not None
        assert await service.verify(full) is not None


async def test_a_key_issued_before_expiry_existed_keeps_working(make_session) -> None:
    """NULL still means never *in the read-model*, and has to.

    Expiring the keys an installation already has would be an outage this change chose on their
    behalf — a silent one, since nothing tells the integration why it stopped. The bound applies
    where keys are **issued**; the ones already out there are a migration for an operator to plan,
    and the console marks them.
    """
    async with make_session() as session:
        service = ApiKeyService(session)
        full, record = await service.create("ada", expires_in_days=0)

        assert record.expires_at is None
        assert await service.verify(full) is not None
