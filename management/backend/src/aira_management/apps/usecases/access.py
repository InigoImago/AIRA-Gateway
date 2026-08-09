"""Who may do what inside one use case — asked once, answered in one place.

The three predicates below are the *whole* authorisation vocabulary of the use-case surface. They
were private methods on the viewset, which was fine while only the viewset asked. The console has
to ask the same questions to decide what to put on screen, and a console that decides for itself
is a console that offers a button the server refuses — which is exactly what happened: a use-case
*user* saw "Add member" and "Remove", clicked one, and got a 403 from a screen that had just
invited the click.

So they live here, and both the enforcement and the presentation read them. Restating them in
TypeScript would have been the same defect with an extra copy to forget.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.rbac import KEYCLOAK_GROUP_PREFIX, has_role
from aira_management.roles import Role

VIEW = "usecases.view_usecase"
CHANGE = "usecases.change_usecase"
MANAGE = "usecases.manage_members"


def may_admin(user: Any, usecase: UseCase) -> bool:
    """May change or delete the use case itself."""
    return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(CHANGE, usecase)


def may_manage(user: Any, usecase: UseCase) -> bool:
    """May change what happens inside it: members, keys, pipeline, budgets, limits."""
    return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(MANAGE, usecase)


def is_member(user: Any, usecase: UseCase) -> bool:
    """True if the caller is an actual member of the use case (or a global admin).

    Deliberately *not* the same as "may see it": the oversight roles (global-admin, it-steuerung,
    it-security) get organisation-wide read visibility through ``scope_queryset``, and read
    visibility must never imply the right to act inside a use case (ADR-0007).
    """
    if has_role(user, Role.GLOBAL_ADMIN):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    if UseCaseMembership.objects.filter(use_case=usecase, user=user).exists():
        return True
    # A group grant makes somebody a member without any row naming them — that is the point of
    # `FRD-209`. Asking only about direct rows here would have let the console offer an API key to
    # somebody the server would refuse, which is the `FRD-206` defect wearing a new hat.
    return UseCaseGroupGrant.objects.filter(
        use_case=usecase, group_path__in=held_group_paths(user)
    ).exists()


def member_queryset(user: Any, queryset: QuerySet[UseCase]) -> QuerySet[UseCase]:
    """Narrow to the use cases this caller is a **member** of.

    The set form of `is_member`, and written beside it so the two cannot drift: a list that answers
    "which ones may I act in" by a different rule than the one the server enforces per object is
    `FRD-206`'s defect in bulk — the console would offer a use case the request is then refused for,
    or withhold one it would have allowed.

    A global admin is a member of everything, exactly as in `is_member`.
    """
    if has_role(user, Role.GLOBAL_ADMIN):
        return queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(
        Q(memberships__user=user) | Q(group_grants__group_path__in=held_group_paths(user))
    ).distinct()


def held_group_paths(user: Any) -> list[str]:
    """The Keycloak group paths this user's last token carried.

    Read back out of the Django groups `sync_user_groups` writes, rather than from the token: the
    predicates here are called from places that have a user and no request, and a permission that
    can only be evaluated where the token happens to be in scope is a permission that gets
    evaluated inconsistently.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    return [
        name[len(KEYCLOAK_GROUP_PREFIX) :]
        for name in user.groups.values_list("name", flat=True)
        if name.startswith(KEYCLOAK_GROUP_PREFIX)
    ]
