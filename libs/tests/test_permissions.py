"""The permission engine (`FRD-614`, `ADR-0025`).

The first test is the one the refactor rests on: for every built-in role and every permission, the
engine must answer what the checks it replaces answered. The expected table is written out by hand
from those checks, one row per permission with the check it came from, rather than derived from the
engine — a table computed from the thing under test agrees with it by construction.
"""

from __future__ import annotations

import pytest

from aira_common.permissions import (
    ALL_PERMISSIONS,
    CATALOGUE,
    GRANTABLE,
    IT_STEUERUNG_DEFAULT,
    Permission,
    RoleDefinition,
    allows,
    builtin_roles,
    effective,
    held_roles,
    parse_permissions,
)
from aira_common.roles import Role

GA = Role.GLOBAL_ADMIN
SEC = Role.IT_SECURITY
STG = Role.IT_STEUERUNG

#: Who held each permission before the engine, and where that was decided.
BEFORE_THE_ENGINE: dict[Permission, tuple[frozenset[Role], str]] = {
    Permission.USECASE_CREATE: (frozenset({GA}), "use-case viewset create: IsGlobalAdmin"),
    Permission.USECASE_READ_ALL: (frozenset({GA, SEC, STG}), "rbac.scope_queryset: oversight"),
    Permission.USECASE_MANAGE_ALL: (frozenset({GA}), "access.may_admin/may_manage/is_member"),
    Permission.USECASE_READ_RETIRED: (frozenset({GA, STG}), "retired list: governance"),
    Permission.USECASE_PURGE: (frozenset({GA}), "purge: has_role(GLOBAL_ADMIN)"),
    Permission.CATALOG_WRITE: (frozenset({GA}), "MayCatalogueModels, gateway providers"),
    Permission.BUDGET_INSTALLATION_WRITE: (frozenset({GA}), "installation budget: IsGlobalAdmin"),
    Permission.REPORT_READ_ALL: (frozenset({GA, SEC, STG}), "gateway visible_scope: oversight"),
    Permission.TRACE_READ_ALL: (frozenset({GA, SEC, STG}), "gateway visible_scope: oversight"),
    Permission.ANOMALY_READ_ALL: (frozenset({GA, SEC, STG}), "gateway visible_scope: oversight"),
    Permission.ANOMALY_RULE_GLOBAL_WRITE: (frozenset({GA, SEC}), "may_author_global: incident"),
    Permission.INCIDENT_SUSPEND: (frozenset({GA, SEC}), "suspensions: may_act_on_incidents"),
    Permission.INCIDENT_INVESTIGATE: (frozenset({GA, SEC}), "source_ip, restriction: incident"),
    Permission.PAYLOAD_READ_ANY: (frozenset({GA, SEC}), "may_read_payload: incident ground"),
    Permission.CONTENT_READ_READ: (frozenset({GA, SEC, STG}), "content-read log: oversight"),
    Permission.OPERATIONS_DIAGNOSE: (frozenset({GA, SEC}), "diagnostics, /readyz: incident"),
    Permission.SMOKETEST_AUTHOR: (frozenset({GA, SEC}), "question catalogue write"),
    Permission.SMOKETEST_RUN_ANY: (frozenset({GA, SEC}), "MayRunTests role half"),
    Permission.DIRECTORY_SEARCH: (frozenset({GA}), "directory search: global-admin half"),
    Permission.ROLE_READ: (frozenset({GA, SEC, STG}), "the platform area: oversight"),
    Permission.ROLE_MANAGE: (frozenset({GA}), "new; the Global Administrator only"),
}

MAPPING = {
    GA: ("/aira/global-admins",),
    SEC: ("/aira/it-security",),
    STG: ("/aira/it-steuerung",),
}


def test_the_table_covers_every_permission() -> None:
    assert set(BEFORE_THE_ENGINE) == set(Permission)


@pytest.mark.parametrize("permission", list(Permission), ids=str)
@pytest.mark.parametrize("role", list(Role), ids=str)
def test_a_builtin_role_answers_what_the_old_checks_answered(
    role: Role, permission: Permission
) -> None:
    holders, where = BEFORE_THE_ENGINE[permission]
    granted = effective(MAPPING[role], builtin_roles(MAPPING))
    assert allows(granted, permission) == (role in holders), where


def test_the_global_administrator_holds_every_permission_by_construction() -> None:
    """Not a stored list: a permission added later is held without anybody ticking it."""
    assert effective(MAPPING[GA], builtin_roles(MAPPING)) == ALL_PERMISSIONS


def test_no_role_but_the_global_administrator_holds_the_reserved_permission() -> None:
    for role in builtin_roles(MAPPING, it_steuerung=ALL_PERMISSIONS):
        assert (Permission.ROLE_MANAGE in role.permissions) == (role.slug == GA)


def test_only_the_global_administrator_and_it_security_are_fixed() -> None:
    fixed = {role.slug for role in builtin_roles(MAPPING) if role.fixed}
    assert fixed == {GA, SEC}
    assert all(role.builtin for role in builtin_roles(MAPPING))


def test_it_steuerung_holds_its_stored_set_and_its_default_without_one() -> None:
    narrowed = frozenset({Permission.REPORT_READ_ALL})
    assert effective(MAPPING[STG], builtin_roles(MAPPING, it_steuerung=narrowed)) == narrowed
    assert effective(MAPPING[STG], builtin_roles(MAPPING)) == IT_STEUERUNG_DEFAULT


def test_an_empty_stored_set_is_empty_rather_than_the_default() -> None:
    """ "Nothing stored" and "stored as nothing" are two answers: narrowing a role to nothing must
    hold."""
    assert effective(MAPPING[STG], builtin_roles(MAPPING, it_steuerung=frozenset())) == frozenset()


def test_permissions_add_up_across_roles() -> None:
    custom = RoleDefinition(
        slug="controlling",
        label="Controlling",
        group_paths=("/finance/controlling",),
        permissions=frozenset({Permission.BUDGET_INSTALLATION_WRITE}),
    )
    roles = (*builtin_roles(MAPPING), custom)
    both = effective(("/aira/it-steuerung", "/finance/controlling"), roles)
    assert both == IT_STEUERUNG_DEFAULT | {Permission.BUDGET_INSTALLATION_WRITE}


def test_a_group_path_matches_exactly_and_never_as_a_prefix() -> None:
    roles = builtin_roles(MAPPING)
    assert effective(("/aira/global-admins-readonly",), roles) == frozenset()
    assert effective(("/aira",), roles) == frozenset()
    assert [role.slug for role in held_roles(("/aira/it-security",), roles)] == [SEC]


def test_no_group_and_no_mapping_confer_nothing() -> None:
    assert effective((), builtin_roles(MAPPING)) == frozenset()
    assert effective(MAPPING[GA], builtin_roles({})) == frozenset()


def test_a_stored_role_can_never_hold_the_reserved_permission_or_an_unknown_one() -> None:
    parsed = parse_permissions(["role.manage", "usecase.create", "no.such.permission"])
    assert parsed == frozenset({Permission.USECASE_CREATE})


def test_every_permission_is_described_and_only_role_manage_is_reserved() -> None:
    assert set(CATALOGUE) == set(Permission)
    assert {Permission.ROLE_MANAGE} == ALL_PERMISSIONS - GRANTABLE
    assert all(info.label and info.area for info in CATALOGUE.values())
