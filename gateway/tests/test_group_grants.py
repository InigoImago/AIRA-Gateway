"""Resolving a token's Keycloak groups into the use cases they reach (`FRD-209`).

The gateway half. What is asserted here is the *decision*, not the SQL: which use cases a caller
gets, what happens when the read-model cannot be read, and that the cache does not outlive a
change by more than it promises.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.auth.grants import CACHE_TTL_SECONDS, GroupGrantResolver
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import UseCaseGroupRead


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _grant(sessions, use_case: str, path: str, role: str = "user") -> None:
    async with sessions() as session:
        session.add(UseCaseGroupRead(use_case_slug=use_case, group_path=path, role=role))
        await session.commit()


# ---- what a group reaches -----------------------------------------------------------------


async def test_a_granted_group_reaches_its_use_case(sessions) -> None:
    """The point of the feature: no row names this caller."""
    await _grant(sessions, "uc-a", "/ai/kundenservice")

    assert await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice"]) == {"uc-a": "user"}


async def test_a_group_nobody_granted_reaches_nothing(sessions) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice")

    assert await GroupGrantResolver(sessions).use_cases(["/ai/vertrieb"]) == {}


async def test_no_groups_at_all_reaches_nothing(sessions) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice")

    assert await GroupGrantResolver(sessions).use_cases([]) == {}


async def test_one_group_can_reach_several_use_cases(sessions) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice", "user")
    await _grant(sessions, "uc-b", "/ai/kundenservice", "admin")

    resolved = await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice"])

    assert resolved == {"uc-a": "user", "uc-b": "admin"}


async def test_two_groups_reaching_one_use_case_take_the_stronger_role(sessions) -> None:
    # An access decision that depended on which row was read first would not be reviewable.
    await _grant(sessions, "uc-a", "/ai/kundenservice", "user")
    await _grant(sessions, "uc-a", "/ai/leads", "admin")

    resolved = await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice", "/ai/leads"])

    assert resolved == {"uc-a": "admin"}


async def test_the_old_convention_resolves_without_any_grant(sessions) -> None:
    """`FRD-102`'s `/use-cases/<slug>` route needs no lookup and keeps working."""
    assert await GroupGrantResolver(sessions).use_cases(["/use-cases/demo-uc"]) == {
        "demo-uc": "user"
    }


async def test_the_two_routes_are_a_union(sessions) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice")

    resolved = await GroupGrantResolver(sessions).use_cases(
        ["/use-cases/demo-uc", "/ai/kundenservice"]
    )

    assert resolved == {"demo-uc": "user", "uc-a": "user"}


# ---- the cache ----------------------------------------------------------------------------


async def test_the_whole_table_is_cached_not_one_query_per_caller(sessions) -> None:
    """Grants are configuration — tens of rows, and every caller's answer comes from the same set.

    A per-caller cache would be a cache with one entry per person and a miss for everybody at once
    when it expired. Asserted by counting sessions: two different callers, one read.
    """
    await _grant(sessions, "uc-a", "/ai/kundenservice")
    await _grant(sessions, "uc-b", "/ai/vertrieb")

    opened = 0

    def counting_sessionmaker():
        nonlocal opened
        opened += 1
        return sessions()

    resolver = GroupGrantResolver(counting_sessionmaker)  # type: ignore[arg-type]
    assert await resolver.use_cases(["/ai/kundenservice"]) == {"uc-a": "user"}
    assert await resolver.use_cases(["/ai/vertrieb"]) == {"uc-b": "user"}

    assert opened == 1


async def test_a_new_grant_is_seen_once_the_cache_expires(sessions) -> None:
    resolver = GroupGrantResolver(sessions)
    assert await resolver.use_cases(["/ai/kundenservice"]) == {}

    await _grant(sessions, "uc-a", "/ai/kundenservice")
    # Still the cached answer — being a few seconds behind is the deliberate cost.
    assert await resolver.use_cases(["/ai/kundenservice"]) == {}

    resolver._loaded_at = time.monotonic() - CACHE_TTL_SECONDS - 0.1
    assert await resolver.use_cases(["/ai/kundenservice"]) == {"uc-a": "user"}


async def test_a_revoked_grant_stops_reaching_once_the_cache_expires(sessions) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice")
    resolver = GroupGrantResolver(sessions)
    assert await resolver.use_cases(["/ai/kundenservice"]) == {"uc-a": "user"}

    async with sessions() as session:
        await session.execute(delete(UseCaseGroupRead))
        await session.commit()

    resolver._loaded_at = time.monotonic() - CACHE_TTL_SECONDS - 0.1
    assert await resolver.use_cases(["/ai/kundenservice"]) == {}


# ---- when the read-model cannot be read ---------------------------------------------------


async def test_an_unreadable_table_refuses_a_grant_rather_than_admitting_it() -> None:
    """The moment a control cannot be evaluated is the worst moment to assume it passes.

    `FRD-405` settled this for rate limits and `FRD-125` for the injection filter; this is the
    same decision for membership.
    """

    class Broken:
        def __call__(self):
            raise RuntimeError("no database")

    resolved = await GroupGrantResolver(Broken()).use_cases(["/ai/kundenservice"])  # type: ignore[arg-type]

    assert resolved == {}


async def test_grants_are_dropped_rather_than_served_stale_when_the_read_fails(sessions) -> None:
    """The interesting half of "refuse rather than admit", and the one a naive test misses.

    Failing on the *first* read is easy: there is nothing cached to serve. What matters is failing
    after a **successful** one — the moment a control stops being evaluable is the moment its last
    answer stops being evidence, and continuing to hand it out would let access outlive the table
    that justified it. Written after the mutation harness pointed out that the first version of
    this test could not tell the two apart.
    """
    await _grant(sessions, "uc-a", "/ai/kundenservice")
    resolver = GroupGrantResolver(sessions)
    assert await resolver.use_cases(["/ai/kundenservice"]) == {"uc-a": "user"}

    def broken():
        raise RuntimeError("the database went away")

    resolver._sessionmaker = broken  # type: ignore[assignment]
    resolver._loaded_at = time.monotonic() - CACHE_TTL_SECONDS - 0.1

    assert await resolver.use_cases(["/ai/kundenservice"]) == {}


async def test_an_unreadable_table_does_not_break_the_convention() -> None:
    """A caller whose membership needs no lookup is unaffected — refusing everybody because a
    read-model table could not be read would turn a distribution problem into a total outage."""

    class Broken:
        def __call__(self):
            raise RuntimeError("no database")

    resolved = await GroupGrantResolver(Broken()).use_cases(["/use-cases/demo-uc"])  # type: ignore[arg-type]

    assert resolved == {"demo-uc": "user"}


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_the_granted_role_is_carried_through(sessions, role: str) -> None:
    await _grant(sessions, "uc-a", "/ai/kundenservice", role)

    assert await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice"]) == {"uc-a": role}
