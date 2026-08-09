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

from django.db.models import QuerySet

from aira_common.access import resolve
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.rbac import KEYCLOAK_GROUP_PREFIX, MANAGE_PERM, VIEW_PERM, has_role
from aira_management.roles import Role

#: Re-exported from `rbac`, which the role gates also read. Two spellings of one permission string
#: is the drift this module was created to stop.
VIEW = VIEW_PERM
CHANGE = "usecases.change_usecase"
MANAGE = MANAGE_PERM


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


def may_call_queryset(user: Any, queryset: QuerySet[UseCase]) -> QuerySet[UseCase]:
    """Narrow to the use cases this caller may attribute **gateway traffic** to.

    This is a third question, and conflating it with either of the other two produced a live defect
    on 2026-08-09. It is not `scope_queryset` (what may I *see*) and it is not `is_member` (what may
    I *administer here*): it is **what will the gateway accept from my token**, and the gateway has
    its own rule — the `/use-cases/<slug>` convention plus group grants, from `aira_common.access`.

    Two consequences that are the whole point:

    - **No global-admin blanket.** `is_member` grants a global administrator everything, because in
      *Management* they may act anywhere. The gateway has no such rule: it reads a token's groups.
      The first version of this function reused `is_member`, so the console offered a global admin
      the alphabetically first of nine hundred use cases and the gateway answered
      `Not a member of use case 'addr-1nn4ss'` — a control that fails the moment it is used, which
      is the `FRD-206` defect this very screen had already had once.
    - **The rule is `aira_common.access.resolve`**, the same function the gateway's
      `GroupGrantResolver` calls. Restating it here in Django would be a second definition of an
      access rule across two planes, which this project has paid for repeatedly.

    An oversight role therefore gets an **empty** answer: it sees every use case and may call none
    (`ADR-0007`). That is not a gap, it is the rule, and the screen says so in words.
    """
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    held = held_group_paths(user)
    grants = list(
        UseCaseGroupGrant.objects.filter(group_path__in=held).values_list(
            "group_path", "use_case__slug", "role"
        )
    )
    return queryset.filter(slug__in=list(resolve(held, grants)))


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
