"""What each role may do, as the gateway reads it (`FRD-614` FR-8).

Driven through the consumer's own handlers into a real read model, so what is asserted is the
decision a caller gets — including when the table cannot be read, and how long a change takes.
"""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.permissions import (
    ALL_PERMISSIONS,
    IT_SECURITY_PERMISSIONS,
    IT_STEUERUNG_DEFAULT,
    Permission,
    effective,
)
from aira_common.roles import Role
from aira_gateway.auth import role_definitions as module
from aira_gateway.auth.dependencies import _with_permissions
from aira_gateway.auth.principal import Principal
from aira_gateway.auth.role_definitions import CACHE_TTL_SECONDS, RoleResolver
from aira_gateway.consumer.apply import HANDLERS
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

MAPPING = {
    Role.GLOBAL_ADMIN: ("/aira/global-admins",),
    Role.IT_SECURITY: ("/aira/it-security",),
    Role.IT_STEUERUNG: ("/aira/it-steuerung",),
}
CONTROLLING = "/finance/controlling"


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _apply(sessions: Any, event_type: str, payload: dict[str, Any]) -> None:
    async with sessions() as session:
        await HANDLERS[event_type](session, payload)
        await session.commit()


async def _role(sessions: Any, permissions: list[str], **overrides: Any) -> None:
    payload = {
        "slug": "controlling",
        "label": "Controlling",
        "group_path": CONTROLLING,
        "permissions": permissions,
        "builtin": False,
    }
    await _apply(sessions, "role.upserted", {**payload, **overrides})


async def _granted(sessions: Any, *groups: str) -> frozenset[Permission]:
    """A fresh resolver per question, so no cache stands between the table and the answer."""
    return effective(groups, await RoleResolver(sessions, MAPPING).definitions())


async def test_a_stored_role_confers_its_permissions_on_its_group_and_nobody_else(sessions) -> None:
    await _role(sessions, ["report.read_all"])

    assert await _granted(sessions, CONTROLLING) == {Permission.REPORT_READ_ALL}
    assert await _granted(sessions, "/finance/controlling-readonly") == frozenset()


async def test_it_steuerung_holds_its_default_until_a_set_is_stored(sessions) -> None:
    assert await _granted(sessions, "/aira/it-steuerung") == IT_STEUERUNG_DEFAULT

    await _role(
        sessions,
        ["report.read_all"],
        slug="it-steuerung",
        label="IT Steuerung",
        group_path="",
        builtin=True,
    )

    assert await _granted(sessions, "/aira/it-steuerung") == {Permission.REPORT_READ_ALL}


async def test_a_change_and_a_removal_are_applied(sessions) -> None:
    await _role(sessions, ["report.read_all"])
    await _role(sessions, ["trace.read_all"])
    assert await _granted(sessions, CONTROLLING) == {Permission.TRACE_READ_ALL}

    await _apply(sessions, "role.removed", {"slug": "controlling"})
    assert await _granted(sessions, CONTROLLING) == frozenset()


async def test_an_event_without_permissions_grants_nothing(sessions) -> None:
    await _role(sessions, ["report.read_all"])
    await _apply(sessions, "role.upserted", {"slug": "controlling", "group_path": CONTROLLING})

    assert await _granted(sessions, CONTROLLING) == frozenset()


async def test_a_row_cannot_confer_the_reserved_permission_or_be_a_fixed_role(sessions) -> None:
    await _role(sessions, ["role.manage", "usecase.create"])
    await _role(sessions, ["usecase.create"], slug="global-admin", group_path="/elsewhere")

    assert await _granted(sessions, CONTROLLING) == {Permission.USECASE_CREATE}
    assert await _granted(sessions, "/elsewhere") == frozenset()


async def test_an_unreadable_table_takes_stored_roles_away_and_leaves_the_fixed_ones() -> None:
    """No `create_all`: the table is not there, which is what a gateway meets before its
    migration ran or while its database is gone."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    resolver = RoleResolver(build_sessionmaker(engine), MAPPING)
    definitions = await resolver.definitions()
    await engine.dispose()

    assert effective(["/aira/it-steuerung", CONTROLLING], definitions) == frozenset()
    assert effective(["/aira/global-admins"], definitions) == ALL_PERMISSIONS
    assert effective(["/aira/it-security"], definitions) == IT_SECURITY_PERMISSIONS


async def test_a_change_is_seen_once_the_cache_expires(sessions, monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    resolver = RoleResolver(sessions, MAPPING)
    await _role(sessions, ["report.read_all"])
    assert effective([CONTROLLING], await resolver.definitions()) == {Permission.REPORT_READ_ALL}

    await _role(sessions, [])
    clock["now"] += CACHE_TTL_SECONDS - 0.5
    assert effective([CONTROLLING], await resolver.definitions()) == {Permission.REPORT_READ_ALL}

    clock["now"] += 1.0
    assert effective([CONTROLLING], await resolver.definitions()) == frozenset()


async def test_a_callers_permissions_and_roles_come_from_the_resolver(sessions) -> None:
    await _role(sessions, ["report.read_all"])
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(role_definitions=RoleResolver(sessions, MAPPING))
        )
    )
    principal = Principal(
        subject="s",
        method="oidc",
        roles=("it-steuerung",),
        groups=("/aira/it-steuerung", CONTROLLING),
    )

    resolved = await _with_permissions(request, principal)  # type: ignore[arg-type]

    assert resolved.roles == ("it-steuerung", "controlling")
    assert resolved.permissions == IT_STEUERUNG_DEFAULT | {Permission.REPORT_READ_ALL}


async def test_without_a_resolver_the_built_in_roles_decide() -> None:
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))
    principal = Principal(subject="s", method="oidc", roles=("it-security",))

    resolved = await _with_permissions(request, principal)  # type: ignore[arg-type]

    assert resolved.permissions == IT_SECURITY_PERMISSIONS
