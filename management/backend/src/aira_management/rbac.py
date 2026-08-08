"""Role-based access control (FRD-201).

Keycloak realm roles are the source of truth: on authentication they are synced onto the
user's Django groups (the five AIRA roles). DRF permission classes gate views by role, and
``scope_queryset`` narrows list results to what the caller may see (governance roles see
everything; others are limited to their object-level permissions via ``django-guardian``).
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Group
from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user
from rest_framework.permissions import BasePermission

from aira_management.roles import ALL_ROLES, GOVERNANCE_ROLES, OVERSIGHT_ROLES, Role

# Roles with organisation-wide read visibility (oversight).
# Defined once in aira_common.roles so the gateway cannot drift from it (ADR-0009).


def sync_user_roles(user: Any, claims: dict[str, Any]) -> None:
    """Make the user's group membership match the realm roles in the token."""
    token_roles = set((claims.get("realm_access") or {}).get("roles", []))
    for role in ALL_ROLES:
        group, _created = Group.objects.get_or_create(name=str(role))
        if str(role) in token_roles:
            user.groups.add(group)
        else:
            user.groups.remove(group)


#: Django groups that mirror a Keycloak group path are prefixed, so they can never collide with
#: the five role groups — a realm with a group literally called `it-security` must not hand out the
#: role by accident.
KEYCLOAK_GROUP_PREFIX = "kc:"


def django_group_name(path: str) -> str:
    """The Django group that carries a Keycloak group's object permissions."""
    return f"{KEYCLOAK_GROUP_PREFIX}{path}"


def sync_user_groups(user: Any, claims: dict[str, Any]) -> None:
    """Make the user's Django group membership match the Keycloak groups in the token.

    This is what makes group grants work at all, and it is the whole trick: `django-guardian`
    already resolves object permissions for a user **and their groups** in one query, so once the
    token's groups are Django groups, `scope_queryset`, `may_admin` and `may_manage` need no change
    whatsoever. A second permission path beside guardian's would be a second chance to forget one —
    which is precisely the mistake the two planes already made about membership (`FRD-209` §1).

    The token is the source of truth on every request, exactly as it is for roles: somebody removed
    from a group in Keycloak loses access on their next token, without anything here being told.
    """
    raw = claims.get("groups")
    paths = {path for path in (raw if isinstance(raw, list) else []) if isinstance(path, str)}
    wanted = {django_group_name(path) for path in paths}

    for name in wanted:
        group, _created = Group.objects.get_or_create(name=name)
        user.groups.add(group)

    # Removed as well as added. A membership that only ever grows is an access list that survives
    # somebody leaving the department, which is the failure this feature exists to prevent.
    stale = user.groups.filter(name__startswith=KEYCLOAK_GROUP_PREFIX).exclude(name__in=wanted)
    for group in stale:
        user.groups.remove(group)


def role_slugs(user: Any) -> set[str]:
    """Return the AIRA role slugs the user currently holds.

    Keycloak-mirror groups are excluded by their prefix: they carry object permissions, not roles,
    and a realm group named after a role must not become one.
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

    PRD §154 gives IT Security "security oversight (restricted view)". The restriction is on
    business content and spend, not on knowing which use cases exist: retention, payload storage,
    filters and limits are precisely the metadata that role oversees. Folding it in with
    `GOVERNANCE_ROLES` gave it an **empty console**, which is not a restricted view.
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


class IsITSecurity(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.IT_SECURITY)


class IsITSteuerung(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.IT_STEUERUNG)


class IsUseCaseAdmin(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.USE_CASE_ADMIN)


class IsUseCaseUser(_HasAnyRole):
    roles = (Role.GLOBAL_ADMIN, Role.USE_CASE_ADMIN, Role.USE_CASE_USER)
