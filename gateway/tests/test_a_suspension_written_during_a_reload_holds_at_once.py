"""A suspension holds at once on the instance that wrote it, even while its cache is reloading.

Suspensions are read through a short-lived cache, and the rule or person writing one invalidates
it, so that stopping traffic is never delayed by the cache's lifetime — only lifting it is, the
harmless direction. An invalidation that landed while another request's reload was waiting for its
query was lost: the reload finished afterwards, stored the rows it had read before the write, and
stamped them fresh, so the instance that had just stopped a caller went on serving them until the
cache lapsed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import aira_gateway.anomalies.suspensions as suspensions_module
from aira_gateway.anomalies.suspensions import AccessSuspension, Suspended, SuspensionService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_a_suspension_written_during_a_reload_is_enforced_at_once(
    sessions, monkeypatch
) -> None:
    service = SuspensionService(sessions, clock=lambda: 100.0)
    load = suspensions_module._load
    read = asyncio.Event()
    answered = asyncio.Event()

    async def slow_load(session: AsyncSession) -> list[AccessSuspension]:
        rows = await load(session)  # what this reload sees: no suspension yet
        read.set()
        await answered.wait()  # the query's answer is still on its way back
        return rows

    monkeypatch.setattr(suspensions_module, "_load", slow_load)
    reload = asyncio.create_task(service.check("demo-uc", "someone-else", None))
    await read.wait()

    # Meanwhile a rule stops ada and invalidates this instance's cache.
    async with sessions() as session:
        session.add(
            AccessSuspension(
                use_case="demo-uc",
                target="subject",
                target_value="ada",
                action="block",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                author="rule:abuse",
                reason="probing",
            )
        )
        await session.commit()
    service.invalidate()

    answered.set()
    await reload
    monkeypatch.setattr(suspensions_module, "_load", load)

    with pytest.raises(Suspended):
        await service.check("demo-uc", "ada", None)
