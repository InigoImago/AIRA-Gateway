"""Role-based access control (`FRD-201`, `ADR-0017`, `ADR-0025`).

**Group membership is the source of truth.** On authentication the token's Keycloak groups are
resolved through the configured mapping and synced onto the user's Django groups; a realm role on
the same token is not read, so assigning one directly grants nothing.

**What a role may do is the permission engine's answer** (`aira_common.permissions`, `FRD-614`).
Every installation-wide check here asks :func:`may` with a `Permission`, never a role: a view that
named a role would be a second place saying who may do what.

What somebody may do *inside* one use case is a grant on that use case, held in
`UseCaseGroupGrant` and enforced through guardian object permissions (`FRD-209`): a predicate that
wants "administers a use case" asks the object, never the token.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user
from rest_framework.permissions import BasePermission

from aira_common.permissions import (
    Permission,
    RoleDefinition,
    allows,
    builtin_roles,
    parse_permissions,
)
from aira_common.roles import parse_role_groups, roles_from_groups
from aira_management.roles import ALL_ROLES, Role

#: The two object permissions a role gate asks about. Defined here rather than in
#: `apps.usecases.access`, which imports this module and re-exports them.
VIEW_PERM = "usecases.view_usecase"
MANAGE_PERM = "usecases.manage_members"

#: Prefix of the Django groups mirroring a Keycloak group path, so they can never collide with the
#: role groups — a realm group literally called `it-security` must not hand out the role.
KEYCLOAK_GROUP_PREFIX = "kc:"


def role_groups() -> dict[Role, tuple[str, ...]]:
    """The configured group → role mapping (`ADR-0017`).

    Read from Django settings on each call rather than at import, because tests configure it per
    case.
    """
    return parse_role_groups(getattr(settings, "AIRA_ROLE_GROUPS", "") or "")


def role_definitions() -> tuple[RoleDefinition, ...]:
    """Every role this installation knows, with what each may do (`FRD-614`).

    The fixed roles from code, IT Steuerung with its stored set (its default when none is stored),
    and every role the installation defined, each conferred by its one group.
    """
    from aira_management.apps.roles.models import StoredRole

    rows = list(StoredRole.objects.all())
    steuerung = next((row for row in rows if row.slug == str(Role.IT_STEUERUNG)), None)
    stored_set = parse_permissions(steuerung.permissions) if steuerung is not None else None
    custom = tuple(
        RoleDefinition(
            slug=row.slug,
            label=row.label,
            group_paths=(row.group_path,) if row.group_path else (),
            permissions=parse_permissions(row.permissions),
        )
        for row in rows
        if not row.builtin
    )
    return builtin_roles(role_groups(), stored_set) + custom


def _token_groups(claims: dict[str, Any]) -> list[str]:
    """The `groups` claim, defensively. A malformed claim confers nothing rather than raising:
    a realm misconfiguration must not stop authentication, it must stop *authority*."""
    raw = claims.get("groups")
    return [path for path in (raw if isinstance(raw, list) else []) if isinstance(path, str)]


def django_group_name(path: str) -> str:
    """The Django group that carries a Keycloak group's object permissions."""
    return f"{KEYCLOAK_GROUP_PREFIX}{path}"


def sync_user_roles(user: Any, claims: dict[str, Any]) -> None:
    """Make the user's Django role groups match the roles their **Keycloak groups** confer.

    Read from the token on every request, so leaving a group in the directory removes the role on
    the next token. Roles are removed as well as added — one only ever granted is one nobody can
    take away.
    """
    held = roles_from_groups(_token_groups(claims), role_groups())
    known = {str(role) for role in ALL_ROLES}
    wanted = {name for name in known if name in held}
    current = set(user.groups.filter(name__in=known).values_list("name", flat=True))
    _reconcile(user, current=current, wanted=wanted)


def sync_user_groups(user: Any, claims: dict[str, Any]) -> None:
    """Make the user's Django group membership match the Keycloak groups in the token.

    This is what makes group grants work: guardian resolves object permissions for a user **and
    their groups** in one query, so once the token's groups are Django groups every predicate works
    unchanged, with no second permission path (`FRD-209` §1). Removed as well as added, so an
    access list does not survive somebody leaving the department.
    """
    wanted = {django_group_name(path) for path in _token_groups(claims)}
    current = set(
        user.groups.filter(name__startswith=KEYCLOAK_GROUP_PREFIX).values_list("name", flat=True)
    )
    _reconcile(user, current=current, wanted=wanted)


def _reconcile(user: Any, *, current: set[str], wanted: set[str]) -> None:
    """Add what is missing and remove what is stale, and touch nothing when they already agree.

    Runs on every authenticated request, so the steady state must not write: a read path that
    writes rules out a read replica and locks `auth_user_groups` rows per request. One function
    for both syncs, so that property has one place to stay true.
    """
    add = wanted - current
    drop = current - wanted
    if not add and not drop:
        return
    # Created only when first added: the role groups by the seed, a Keycloak mirror group the
    # first time somebody in it signs in.
    for name in sorted(add):
        group, _created = Group.objects.get_or_create(name=name)
        user.groups.add(group)
    if drop:
        user.groups.remove(*Group.objects.filter(name__in=drop))


def role_slugs(user: Any) -> set[str]:
    """The AIRA role slugs the user currently holds.

    Keycloak-mirror groups are excluded by their prefix: they carry object permissions, not roles.
    """
    return {
        name
        for name in user.groups.values_list("name", flat=True)
        if not name.startswith(KEYCLOAK_GROUP_PREFIX)
    }


def held_roles_of(user: Any) -> tuple[RoleDefinition, ...]:
    """The roles this user holds — every one of them through a Keycloak group.

    A built-in role through the Django group `sync_user_roles` keeps equal to the token; a stored
    role through the mirror of its group `sync_user_groups` keeps. One read of the user's groups
    answers both.
    """
    if not getattr(user, "is_authenticated", False):
        return ()
    names = list(user.groups.values_list("name", flat=True))
    slugs = {name for name in names if not name.startswith(KEYCLOAK_GROUP_PREFIX)}
    paths = {name[len(KEYCLOAK_GROUP_PREFIX) :] for name in names if name not in slugs}
    return tuple(
        role
        for role in role_definitions()
        if (role.slug in slugs if role.builtin else any(p in paths for p in role.group_paths))
    )


def permissions_of(user: Any) -> frozenset[Permission]:
    """Everything this user may do across the installation: the union over their roles
    (`FRD-614` FR-7). Nothing for an anonymous user."""
    granted: frozenset[Permission] = frozenset()
    for role in held_roles_of(user):
        granted |= role.permissions
    return granted


def may(user: Any, permission: Permission) -> bool:
    """The one installation-wide question every view asks."""
    return allows(permissions_of(user), permission)


def scope_queryset(user: Any, perm: str, queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Return only the objects the user may see: all with `usecase.read_all` or
    `usecase.manage_all`, else guardian-permitted.

    Administering every use case includes seeing them — a use case nobody can reach is one nobody
    can administer. The converse does not hold: seeing never implies acting (`ADR-0007`).
    """
    granted = permissions_of(user)
    if Permission.USECASE_READ_ALL in granted or Permission.USECASE_MANAGE_ALL in granted:
        return queryset
    return get_objects_for_user(user, perm, klass=queryset)


class _Requires(BasePermission):
    permission: Permission

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return may(request.user, self.permission)


#: One DRF permission class per permission, made once so a view and a test name the same class.
_REQUIRES: dict[Permission, type[_Requires]] = {
    permission: type(f"Requires_{permission.name}", (_Requires,), {"permission": permission})
    for permission in Permission
}


def requires(permission: Permission) -> type[BasePermission]:
    """The DRF permission class that lets a request through when its user holds ``permission``."""
    return _REQUIRES[permission]


class MaySearchDirectory(BasePermission):
    """`directory.search`, or somebody who administers **at least one** use case (`ADR-0017`).

    Administering a use case is a grant on *that* use case, so this asks the object grants. The
    people who add members need the person and group picker.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if may(user, Permission.DIRECTORY_SEARCH):
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        # A live use case: retiring one ends every access it granted (`FRD-607`).
        live = UseCase.objects.filter(deleted_at__isnull=True)
        return get_objects_for_user(user, MANAGE_PERM, klass=live).exists()


class MayRunTests(BasePermission):
    """Who may reach the question catalogue at all (`FRD-504`, `ADR-0020`).

    The **door**, not the decision: it answers whether any use case exists this person could put
    the catalogue to. Which use case, and whether that one is runnable, is
    `access.may_run_tests_queryset`, asked per object by every endpoint — a class-level permission
    cannot see an object.

    `smoketest.run_any`, or an administrator of some use case. A normal use-case user is
    deliberately not among them: a run spends the use case's budget a hundred prompts at a time, a
    decision about the use case rather than work inside it.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.access import may_run_tests_queryset
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if may(user, Permission.SMOKETEST_RUN_ANY):
            return True
        return may_run_tests_queryset(user, UseCase.objects.all()).exists()
