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
from aira_gateway.db.models import UseCaseGroupRead, UseCaseMemberRead


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


async def _member(sessions, use_case: str, username: str, role: str = "user") -> None:
    """A grant naming one **person** — the other half of `FRD-209` §2.1."""
    async with sessions() as session:
        session.add(UseCaseMemberRead(use_case_slug=use_case, subject=username, role=role))
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


# ---- what a name reaches ------------------------------------------------------------------


async def test_a_grant_naming_a_person_reaches_its_use_case(sessions) -> None:
    """The half that was missing, and it is `FR-6`: *the union of the convention, the group grants,
    and the user grants naming them*.

    Management wrote the row, Kafka carried it, `use_case_members` held it — and the resolver read
    only the group table, so somebody added to a use case by name was refused by the gateway while
    the console listed them as its administrator. Reported from the console exactly that way.
    """
    await _member(sessions, "uc-a", "erika", "admin")

    assert await GroupGrantResolver(sessions).use_cases([], "erika") == {"uc-a": "admin"}


async def test_a_name_that_matches_nobody_reaches_nothing(sessions) -> None:
    await _member(sessions, "uc-a", "erika")

    assert await GroupGrantResolver(sessions).use_cases([], "someone-else") == {}
    assert await GroupGrantResolver(sessions).use_cases([], None) == {}


async def test_a_person_named_and_grouped_takes_the_stronger_role(sessions) -> None:
    """Union, not precedence — and which table was read first is not a thing access may depend on
    (`FRD-209` §2.1). Asserted in **both** directions, because a rule that only holds one way round
    is a rule about row order wearing a different hat."""
    await _grant(sessions, "uc-a", "/ai/kundenservice", "user")
    await _member(sessions, "uc-a", "erika", "admin")
    await _grant(sessions, "uc-b", "/ai/kundenservice", "admin")
    await _member(sessions, "uc-b", "erika", "user")

    reached = await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice"], "erika")

    assert reached == {"uc-a": "admin", "uc-b": "admin"}


async def test_a_person_reaches_a_use_case_no_group_of_theirs_does(sessions) -> None:
    """The reported case in one line: no relevant group at all, and a membership by name."""
    await _grant(sessions, "uc-a", "/ai/vertrieb")
    await _member(sessions, "uc-b", "erika")

    assert await GroupGrantResolver(sessions).use_cases(["/ai/kundenservice"], "erika") == {
        "uc-b": "user"
    }


async def test_a_named_grant_is_dropped_rather_than_served_stale_when_the_read_fails(
    sessions,
) -> None:
    """Same direction as the group half, and the same trap: failing on the **first** read proves
    nothing, because there is nothing cached to serve.

    Written that way first — a fresh resolver against a broken database — and the mutation that
    deletes `self._members = ()` from the failure path left it green, because the list it was
    supposed to notice being cleared had never been filled. A permission that can no longer be
    verified is not still granted, and that has to hold *after* a good read."""
    await _member(sessions, "uc-a", "erika")
    resolver = GroupGrantResolver(sessions)
    assert await resolver.use_cases([], "erika") == {"uc-a": "user"}

    def broken():  # noqa: ANN202
        raise RuntimeError("the database went away")

    resolver._sessionmaker = broken  # type: ignore[assignment]
    resolver._loaded_at = time.monotonic() - CACHE_TTL_SECONDS - 0.1

    assert await resolver.use_cases([], "erika") == {}


# ---- and the caller of all of it ----------------------------------------------------------


async def test_a_token_with_no_groups_still_has_its_name_looked_up(sessions) -> None:
    """The resolver being right is not enough if nothing asks it.

    `_with_group_grants` returned early on `not principal.groups`, so the person this feature is
    for — added to a use case **by name**, in no relevant Keycloak group — left before the lookup
    that would have found them. The resolver tests above all passed while that was true: they call
    the resolver directly, which is the trap of testing a component instead of the path.
    """
    from types import SimpleNamespace

    from aira_gateway.auth.dependencies import _with_group_grants
    from aira_gateway.auth.principal import Principal

    await _member(sessions, "uc-a", "erika", "admin")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(group_grants=GroupGrantResolver(sessions)))
    )
    principal = Principal(subject="kc-uuid-1", method="oidc", username="erika", groups=())

    resolved = await _with_group_grants(request, principal)  # type: ignore[arg-type]

    assert resolved.use_cases == ("uc-a",)


async def test_a_token_naming_nobody_and_holding_nothing_is_left_alone(sessions) -> None:
    """A guard on the guard: with neither groups nor a username there is nothing to look anything
    up by, and the early return that remains must not have been removed wholesale."""
    from types import SimpleNamespace

    from aira_gateway.auth.dependencies import _with_group_grants
    from aira_gateway.auth.principal import Principal

    await _member(sessions, "uc-a", "erika")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(group_grants=GroupGrantResolver(sessions)))
    )
    principal = Principal(subject="kc-uuid-2", method="oidc", username=None, groups=())

    assert (await _with_group_grants(request, principal)).use_cases == ()  # type: ignore[arg-type]


async def test_the_role_the_resolver_worked_out_reaches_the_principal(sessions) -> None:
    """**The wire, not the ends.**

    `test_the_granted_role_is_carried_through` above proves the resolver answers with a role.
    `_with_group_grants` then took `granted.keys()` and dropped the values, so the one fact only
    this layer can establish — *as what* — was computed, asserted, and discarded one call later.
    `payloads.grant_role_in` re-derived it from `use_case_members`, where a group grant writes no
    row, and answered `user` for an administrator.

    Both ends were individually correct and individually tested, which is exactly why nothing
    failed. Removing `grants=` from the call below leaves every other test in this file and every
    test in `test_payload_access.py` green — those construct a `Principal` themselves — so this
    assertion is the only thing standing between the two halves.
    """
    from types import SimpleNamespace

    from aira_gateway.auth.dependencies import _with_group_grants
    from aira_gateway.auth.principal import Principal

    await _grant(sessions, "uc-a", "/ai/kundenservice", "admin")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(group_grants=GroupGrantResolver(sessions)))
    )
    principal = Principal(
        subject="kc-uuid-3", method="oidc", username="boss", groups=("/ai/kundenservice",)
    )

    resolved = await _with_group_grants(request, principal)  # type: ignore[arg-type]

    assert resolved.use_cases == ("uc-a",)
    assert resolved.grants == (("uc-a", "admin"),), (
        "the slug reached the principal and the role it was granted with did not"
    )
