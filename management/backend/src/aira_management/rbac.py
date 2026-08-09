"""Role-based access control (FRD-201, ADR-0017).

**Group membership is the source of truth.** On authentication the Keycloak groups in the token
are resolved through the configured mapping and synced onto the user's Django groups. A realm role
on the same token is not read: assigning one directly grants nothing, which is the guarantee that
made group membership a single point of truth rather than a convention.

Only three roles arrive this way. `use-case-admin` and `use-case-user` are not organisation-wide
facts about a person — they are a group's relationship to *one* use case, held in
`UseCaseGroupGrant` and enforced through `django-guardian` object permissions (`FRD-209`). A
predicate that wants "administers a use case" asks the object, never the token.

DRF permission classes gate views by role, and ``scope_queryset`` narrows list results to what the
caller may see (oversight roles see everything; others are limited to their object-level
permissions).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user
from rest_framework.permissions import BasePermission

from aira_common.roles import parse_role_groups, roles_from_groups
from aira_management.roles import ALL_ROLES, GOVERNANCE_ROLES, OVERSIGHT_ROLES, Role

#: The two object permissions a role gate ever has to ask about. Defined here rather than in
#: `apps.usecases.access` because that module imports *this* one — and one definition is the whole
#: reason `access.py` exists (`FRD-206`: a predicate restated is a predicate that drifts).
VIEW_PERM = "usecases.view_usecase"
MANAGE_PERM = "usecases.manage_members"

# Roles with organisation-wide read visibility (oversight).
# Defined once in aira_common.roles so the gateway cannot drift from it (ADR-0009).


def role_groups() -> dict[Role, tuple[str, ...]]:
    """The configured group → role mapping (`ADR-0017`).

    Read from Django settings on each call rather than captured at import: the tests configure it
    per case, and a module-level snapshot would make the first test's mapping everybody's.
    """
    return parse_role_groups(getattr(settings, "AIRA_ROLE_GROUPS", "") or "")


def sync_user_roles(user: Any, claims: dict[str, Any]) -> None:
    """Make the user's Django groups match the roles their **Keycloak groups** confer.

    The token is the source of truth on every request, so somebody removed from the group in the
    directory loses the role on their next token without anything here being told — the same
    property `sync_user_groups` relies on for use-case access.

    Every role is removed as well as added. A role that is only ever granted is one nobody can
    take away, and the whole point of reading this from the directory is that the directory
    decides.
    """
    held = roles_from_groups(_token_groups(claims), role_groups())
    for role in ALL_ROLES:
        group, _created = Group.objects.get_or_create(name=str(role))
        if str(role) in held:
            user.groups.add(group)
        else:
            user.groups.remove(group)


def _token_groups(claims: dict[str, Any]) -> list[str]:
    """The `groups` claim, defensively. A malformed claim confers nothing rather than raising:
    a realm misconfiguration must not stop authentication, it must stop *authority*."""
    raw = claims.get("groups")
    return [path for path in (raw if isinstance(raw, list) else []) if isinstance(path, str)]


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


class IsGlobalAdminOrUseCaseAdministrator(BasePermission):
    """A Global Administrator, or somebody who administers **at least one** use case (`ADR-0017`).

    `use-case-admin` was a realm role and is not one any more: administering a use case is a group
    grant on *that* use case. The question this class asks is the one the old role approximated —
    "is this person an administrator anywhere" — and it asks the object grants, which is where the
    answer has actually lived since `FRD-209`.

    Used by the directory search. Narrowing it to Global Administrators would take the group and
    person picker away from exactly the people who add members, which is `FRD-206`'s defect
    inverted: a capability with no way in, and that kind does not announce itself.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if has_role(user, Role.GLOBAL_ADMIN):
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase).exists()


class MayTestModels(BasePermission):
    """Who may put a battery of questions to a model (`FRD-504`).

    Three roles rather than one set already in use, and each is here for its own reason: a Global
    Administrator runs the installation, IT Security asks whether a model is fit to be trusted, and
    a **use-case administrator** asks whether it is fit for their use case — which is the person
    who most often wants to know.

    Not `IsITSecurity`: running a battery also needs *membership* of a use case to attribute the
    traffic to, and IT Security is deliberately a member of nothing (`ADR-0007`). Requiring both
    made the feature unusable by everybody, which is the clearest sign that the two requirements
    were never the same requirement.

    **Re-derived for `ADR-0017`.** The third clause used to be the `use-case-admin` realm role.
    That role no longer exists, and the honest replacement is not "administers a use case" but
    `FRD-504`'s own sentence — *whoever may call a model may test one* — which is: belongs to at
    least one use case. Narrowing it to administrators would have taken the feature away from
    people the previous rule included, silently, while looking like a faithful translation.
    """

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        from aira_management.apps.usecases.models import UseCase

        user = request.user
        if has_role(user, Role.GLOBAL_ADMIN, Role.IT_SECURITY):
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        return get_objects_for_user(user, VIEW_PERM, klass=UseCase).exists()
