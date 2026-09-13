"""Role-based access control (`FRD-201`, `ADR-0017`).

**Group membership is the source of truth.** On authentication the token's Keycloak groups are
resolved through the configured mapping and synced onto the user's Django groups; a realm role on
the same token is not read, so assigning one directly grants nothing.

Only three roles arrive this way. `use-case-admin` and `use-case-user` are a group's relationship
to *one* use case, held in `UseCaseGroupGrant` and enforced through guardian object permissions
(`FRD-209`): a predicate that wants "administers a use case" asks the object, never the token.

DRF permission classes gate views by role, and ``scope_queryset`` narrows lists to what the caller
may see.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user
from rest_framework.permissions import BasePermission

from aira_common.roles import parse_role_groups, roles_from_groups
from aira_management.roles import (
    ALL_ROLES,
    CATALOG_ROLES,
    GOVERNANCE_ROLES,
    OVERSIGHT_ROLES,
    Role,
)

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


def has_role(user: Any, *roles: Role) -> bool:
    """True if the (authenticated) user holds any of ``roles``."""
    if not user.is_authenticated:
        return False
    slugs = role_slugs(user)
    return any(str(role) in slugs for role in roles)


def has_governance_role(user: Any) -> bool:
    return has_role(user, *GOVERNANCE_ROLES)


def has_oversight_role(user: Any) -> bool:
    """Whether this user may see every use case — a wider set than may see every figure.

    IT Security's "restricted view" (PRD §154) restricts business content and spend, not knowing
    which use cases exist: their retention, storage, filters and limits are what it oversees.
    """
    return has_role(user, *OVERSIGHT_ROLES)


def scope_queryset(user: Any, perm: str, queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Return only the objects the user may see: all for oversight, else guardian-permitted."""
    if has_oversight_role(user):
        return queryset
    return get_objects_for_user(user, perm, klass=queryset)


class _HasAnyRole(BasePermission):
    roles: tuple[Role, ...] = ()

    def has_permission(self, request: Any, view: Any) -> bool:
        return has_role(request.user, *self.roles)


class IsGlobalAdmin(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN,)


class MayCatalogueModels(_HasAnyRole):
    """Who may declare a model, price it and release it for use (`FRD-307`).

    The same set as `IsGlobalAdmin` today, and a separate name because the gateway guards a
    question by the same rule and takes it from the same definition, `CATALOG_ROLES`
    (`test_catalog_roles_are_one_definition`, `FRD-503`).
    """

    roles = tuple(sorted(CATALOG_ROLES))


class IsITSecurity(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.IT_SECURITY)


class IsITSteuerung(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.IT_STEUERUNG)


class IsGlobalAdminOrUseCaseAdministrator(BasePermission):
    """A Global Administrator, or somebody who administers **at least one** use case (`ADR-0017`).

    Administering a use case is a grant on *that* use case, so this asks the object grants. Used
    by the directory search: the people who add members need the person and group picker.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if has_role(user, Role.GLOBAL_ADMIN):
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase).exists()


class MayRunTests(BasePermission):
    """Who may reach the question catalogue at all (`FRD-504`, `ADR-0020`).

    The **door**, not the decision: it answers whether any use case exists this person could put
    the catalogue to. Which use case, and whether that one is runnable, is
    `access.may_run_tests_queryset`, asked per object by every endpoint — a class-level permission
    cannot see an object.

    A Global Administrator, IT Security, and an administrator of some use case. A normal use-case
    user is deliberately not among them: a run spends the use case's budget a hundred prompts at a
    time, a decision about the use case rather than work inside it.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.access import may_run_tests_queryset
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if has_role(user, Role.GLOBAL_ADMIN, Role.IT_SECURITY):
            return True
        return may_run_tests_queryset(user, UseCase.objects.all()).exists()
