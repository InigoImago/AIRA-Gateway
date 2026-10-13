"""Who may do what inside one use case — asked once, answered in one place (`FRD-206`).

These predicates are the whole authorisation vocabulary of the use-case surface. The server
enforces them and the console reads them (through `UseCaseSerializer.permissions`) to decide what to
offer, so a screen never offers a button the server refuses. Restating them in TypeScript would be
the same defect with an extra copy to forget.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db.models import QuerySet
from guardian.core import ObjectPermissionChecker
from guardian.shortcuts import get_objects_for_user

from aira_common.access import resolve
from aira_common.permissions import Permission
from aira_management.apps.usecases.models import (
    UseCase,
    UseCaseGroupGrant,
    UseCaseMembership,
    UseCaseQuerySet,
)
from aira_management.memo import remembered
from aira_management.rbac import MANAGE_PERM, VIEW_PERM, may, paths_behind

#: Re-exported from `rbac`, which the role gates also read: one spelling per permission string.
VIEW = VIEW_PERM
CHANGE = "usecases.change_usecase"
MANAGE = MANAGE_PERM


def may_admin(user: Any, usecase: UseCase) -> bool:
    """May change or delete the use case itself. Nobody may, once it is retired (`FRD-607`)."""
    return not usecase.is_deleted and _holds(user, CHANGE, usecase)


def may_manage(user: Any, usecase: UseCase) -> bool:
    """May change what happens inside it: members, keys, pipeline, budgets, limits.

    Nobody may, once it is retired: retiring one ends every access it granted (`FRD-607`), and
    its guardian rows stay behind as evidence — so this asks the tombstone as well as the rows,
    for a route that reaches the use case without the viewset's live filter.
    """
    return not usecase.is_deleted and _holds(user, MANAGE, usecase)


def _holds(user: Any, perm: str, usecase: UseCase) -> bool:
    """`usecase.manage_all`, or the object permission ``perm`` on ``usecase``."""
    if not getattr(user, "is_authenticated", False):
        return False
    if may(user, Permission.USECASE_MANAGE_ALL):
        return True
    return _object_permissions(user).has_perm(perm, usecase)


def _object_permissions(user: Any) -> ObjectPermissionChecker:
    """Guardian's checker for ``user``, one per request: it answers every permission on an object
    from one read, where `user.has_perm` builds a new checker — two queries — for each question."""
    return remembered(("object_permissions", user.pk), lambda: ObjectPermissionChecker(user))


def prefetch_object_permissions(user: Any, usecases: Iterable[UseCase]) -> None:
    """Read ``user``'s object permissions on all of ``usecases`` at once — a page of the list."""
    page = list(usecases)
    if page and getattr(user, "is_authenticated", False):
        # Guardian reads any iterable of instances; its annotation names only a queryset.
        _object_permissions(user).prefetch_perms(page)  # type: ignore[arg-type]


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
    return usecase.pk in _granted_use_cases(user)


def _granted_use_cases(user: Any) -> frozenset[int]:
    """The ids of every use case a membership or a held group's grant puts ``user`` in."""

    def granted() -> frozenset[int]:
        direct = UseCaseMembership.objects.filter(user=user).values_list("use_case_id", flat=True)
        through_groups = UseCaseGroupGrant.objects.filter(
            group_path__in=held_group_paths(user)
        ).values_list("use_case_id", flat=True)
        return frozenset([*direct, *through_groups])

    return remembered(("granted_use_cases", user.pk), granted)


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
    return queryset.filter(slug__in=sorted(_callable_slugs(user)))


def may_call(user: Any, usecase: UseCase) -> bool:
    """`may_call_queryset` for one use case, from the same answer."""
    if not getattr(user, "is_authenticated", False):
        return False
    return usecase.slug in _callable_slugs(user)


def _callable_slugs(user: Any) -> frozenset[str]:
    """The slugs `aira_common.access.resolve` gives ``user`` — the gateway's own rule."""

    def callable_slugs() -> frozenset[str]:
        held = held_group_paths(user)
        grants = list(
            UseCaseGroupGrant.objects.filter(group_path__in=held).values_list(
                "group_path", "use_case__slug", "role"
            )
        )
        # And the memberships naming this person (`FRD-209` §2.1), keyed by username as the
        # gateway keys them, so both planes answer this question alike.
        direct = list(
            UseCaseMembership.objects.filter(user=user).values_list("use_case__slug", "role")
        )
        return frozenset(resolve(held, grants, direct))

    return remembered(("callable_slugs", user.pk), callable_slugs)


def may_run_tests_queryset(user: Any, queryset: UseCaseQuerySet) -> QuerySet[UseCase]:
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
    reachable = may_call_queryset(user, queryset.live())
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
    these predicates are called where there is a user and no request — through `paths_behind`,
    which also recognises a mirror named by its path's digest.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    return remembered(
        ("group_paths", user.pk),
        lambda: paths_behind(user.groups.values_list("name", flat=True)),
    )
