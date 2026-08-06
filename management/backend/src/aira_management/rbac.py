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

from aira_management.roles import ALL_ROLES, GOVERNANCE_ROLES, Role

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


def role_slugs(user: Any) -> set[str]:
    """Return the AIRA role slugs the user currently holds (their group names)."""
    return set(user.groups.values_list("name", flat=True))


def has_role(user: Any, *roles: Role) -> bool:
    """True if the (authenticated) user holds any of ``roles``."""
    if not user.is_authenticated:
        return False
    slugs = role_slugs(user)
    return any(str(role) in slugs for role in roles)


def has_governance_role(user: Any) -> bool:
    return has_role(user, *GOVERNANCE_ROLES)


def scope_queryset(user: Any, perm: str, queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Return only the objects the user may see: all for governance, else guardian-permitted."""
    if has_governance_role(user):
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
