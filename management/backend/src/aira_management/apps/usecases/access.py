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

from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.rbac import has_role
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
    return UseCaseMembership.objects.filter(use_case=usecase, user=user).exists()
