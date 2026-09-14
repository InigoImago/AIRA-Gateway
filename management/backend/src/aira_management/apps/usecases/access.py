"""Who may do what inside one use case — asked once, answered in one place (`FRD-206`).

These predicates are the whole authorisation vocabulary of the use-case surface. The server
enforces them and the console reads them (through `UseCaseSerializer.permissions`) to decide what to
offer, so a screen never offers a button the server refuses. Restating them in TypeScript would be
the same defect with an extra copy to forget.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user

from aira_common.access import resolve
from aira_common.permissions import Permission
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.rbac import KEYCLOAK_GROUP_PREFIX, MANAGE_PERM, VIEW_PERM, may

#: Re-exported from `rbac`, which the role gates also read: one spelling per permission string.
VIEW = VIEW_PERM
CHANGE = "usecases.change_usecase"
MANAGE = MANAGE_PERM


def may_admin(user: Any, usecase: UseCase) -> bool:
    """May change or delete the use case itself."""
    return may(user, Permission.USECASE_MANAGE_ALL) or user.has_perm(CHANGE, usecase)


def may_manage(user: Any, usecase: UseCase) -> bool:
    """May change what happens inside it: members, keys, pipeline, budgets, limits."""
    return may(user, Permission.USECASE_MANAGE_ALL) or user.has_perm(MANAGE, usecase)


def holds_a_grant(user: Any, usecase: UseCase) -> bool:
    """True if a **grant** puts this person inside the use case — no role blanket.

    The two things that can be taken away: a membership row naming them, and a group grant
    matching a path their last token carried (`FRD-209`). Separate from :func:`is_member`, whose
    Global Administrator blanket is wrong wherever the question is *whose* the use case is:

    - a credential's owner must be somebody the use case can be asked about (`FRD-604`);
    - when access ends, the keys that rested on it end with it — a blanket that never ends would
      keep every key alive (`FRD-613`).
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if UseCaseMembership.objects.filter(use_case=usecase, user=user).exists():
        return True
    return UseCaseGroupGrant.objects.filter(
        use_case=usecase, group_path__in=held_group_paths(user)
    ).exists()


def is_member(user: Any, usecase: UseCase) -> bool:
    """True if the caller may act inside the use case — a grant, or `usecase.manage_all`.

    Deliberately *not* "may see it": `usecase.read_all` sees every use case through
    ``scope_queryset``, and read visibility must never imply the right to act (`ADR-0007`).
    """
    return may(user, Permission.USECASE_MANAGE_ALL) or holds_a_grant(user, usecase)


def may_call_queryset(user: Any, queryset: QuerySet[UseCase]) -> QuerySet[UseCase]:
    """Narrow to the use cases this caller may attribute **gateway traffic** to.

    A third question, neither `scope_queryset` (what may I *see*) nor `is_member` (what may I
    *administer*): **what will the gateway accept from my token**. So:

    - **no Global Administrator blanket** — the gateway has no such rule, it reads a token's
      groups, and a console offering a use case the gateway refuses is the `FRD-206` defect;
    - **the rule is `aira_common.access.resolve`**, the function the gateway's
      `GroupGrantResolver` calls, so the two planes cannot hold two definitions of it.

    An oversight role therefore gets an empty answer: it sees every use case and may call none
    (`ADR-0007`).
    """
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    held = held_group_paths(user)
    grants = list(
        UseCaseGroupGrant.objects.filter(group_path__in=held).values_list(
            "group_path", "use_case__slug", "role"
        )
    )
    # And the memberships naming this person (`FRD-209` §2.1), keyed by username as the gateway
    # keys them, so both planes answer this question alike.
    direct = list(UseCaseMembership.objects.filter(user=user).values_list("use_case__slug", "role"))
    return queryset.filter(slug__in=list(resolve(held, grants, direct)))


def may_run_tests_queryset(user: Any, queryset: QuerySet[UseCase]) -> QuerySet[UseCase]:
    """Narrow to the use cases this caller may put the question catalogue to (`FRD-504`).

    Two conditions, both necessary:

    1. `may_call_queryset` — a run is real traffic sent with the caller's own credentials, so a
       use case they cannot call would refuse its first question. A role is no bypass of this.
    2. **Administration, not membership**: running the catalogue spends the use case's budget a
       hundred prompts at a time, a decision about the use case rather than work inside it. So an
       administrator of *that* use case (`MANAGE`), or `smoketest.run_any` — a permission rather
       than a grant, because IT Security holds it and is deliberately a member of nothing
       (`ADR-0007`).
    """
    # Only a live use case is run: retiring one ends every access it granted (`FRD-607`).
    reachable = may_call_queryset(user, queryset.filter(deleted_at__isnull=True))
    if may(user, Permission.SMOKETEST_RUN_ANY):
        return reachable
    if not getattr(user, "is_authenticated", False):
        return reachable.none()
    return get_objects_for_user(user, MANAGE, klass=reachable)


def may_run_tests(user: Any, usecase: UseCase) -> bool:
    """`may_run_tests_queryset` for one use case — asked through the queryset, so one rule."""
    return may_run_tests_queryset(user, UseCase.objects.filter(pk=usecase.pk)).exists()


def held_group_paths(user: Any) -> list[str]:
    """The Keycloak group paths this user's last token carried.

    Read back from the Django groups `sync_user_groups` writes rather than from the token, because
    these predicates are called where there is a user and no request.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    return [
        name[len(KEYCLOAK_GROUP_PREFIX) :]
        for name in user.groups.values_list("name", flat=True)
        if name.startswith(KEYCLOAK_GROUP_PREFIX)
    ]
