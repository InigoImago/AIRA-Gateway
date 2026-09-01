"""Phase 0 seed contribution: the three roles (as Groups) and the five demo users.

Deterministic and idempotent: users are keyed by a fixed username, groups by role name,
so re-running produces the same state. ``fresh`` removes existing demo users first.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from aira_management.apps.api.models import OidcIdentity, PendingIdentity
from aira_management.apps.seed.registry import SeedResult, register
from aira_management.roles import Role

DEMO_DOMAIN = "demo.aira"

#: The demo people, and the organisation-wide role each holds — `None` for the two who hold none.
#:
#: `ucadmin` and `ucuser` used to be listed under `use-case-admin` and `use-case-user`, which
#: `ADR-0017` abolished a day before this table was corrected. What they can do comes from grants
#: on individual use cases (`FRD-209`), which is exactly what makes them useful in a walkthrough:
#: switching to `ucadmin` shows three of four use cases *because of a grant*, not because of a
#: badge. Leaving the badge in place made the demo argue for the mechanism it had replaced.
DEMO_USERS: dict[str, Role | None] = {
    "admin": Role.GLOBAL_ADMIN,
    "itsec": Role.IT_SECURITY,
    "itgov": Role.IT_STEUERUNG,
    "ucadmin": None,
    "ucuser": None,
}


@register(name="roles_and_users", order=10)
def seed_roles_and_users(fresh: bool) -> SeedResult:
    """Create role Groups and one demo user per role. Idempotent."""
    user_model = get_user_model()

    if fresh:
        user_model.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").delete()

    groups: dict[Role, Group] = {}
    groups_created = 0
    for role in Role:
        group, created = Group.objects.get_or_create(name=str(role))
        groups[role] = group
        groups_created += int(created)

    # Groups for roles that no longer exist. This contribution created them, so it takes them
    # away: `use-case-admin` and `use-case-user` were roles until `ADR-0017`, and a Django group
    # named after an abolished role is a row that can still be assigned by hand and confers
    # nothing. Named explicitly rather than "anything not in `Role`", because deleting whatever
    # this seed does not recognise would take the `kc:/…` groups access is granted through.
    retired = Group.objects.filter(name__in=["use-case-admin", "use-case-user"])
    groups_retired = retired.count()
    retired.delete()

    role_groups = list(groups.values())

    users_created = 0
    invited = 0
    # `held` rather than `role`: the loop above binds `role` to a `Role`, and this one to a role
    # **or none**. Reusing the name made mypy right about something a reader would also trip on.
    for username, held in DEMO_USERS.items():
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@{DEMO_DOMAIN}"},
        )
        if created:
            users_created += 1
        # **No `is_superuser`, no `is_staff`, and no password** — set, and then cleared here on
        # every run, because an installation that ran an older seed is carrying all three.
        #
        # The flag was the whole of `ADR-0017` undone in one column. `may_admin` and `may_manage`
        # ask `user.has_perm(…, usecase)`, and Django answers **True for every permission** to a
        # superuser before any backend is consulted — guardian's `get_objects_for_user` short-
        # circuits the same way. So the seeded `admin` administered every use case that would ever
        # exist, from a fact stored *here* rather than read from the directory: taking the role
        # group away in Keycloak removed the role and changed nothing about what they could do.
        # That is precisely the second answer to "who may do what" this project abolished roles
        # to avoid, and it outranked the first.
        #
        # The password is inert — there is no session login, no admin site and no password
        # backend on this API (`config/settings.py`) — which is exactly why it should not be
        # there: a known credential on the most privileged account in the installation, kept
        # against the day somebody adds the login that would make it work. Console sign-in is
        # Keycloak's, and the realm keeps its own demo passwords.
        stale = [field for field in ("is_staff", "is_superuser") if getattr(user, field, False)]
        if stale or user.has_usable_password():
            user.is_staff = False
            user.is_superuser = False
            user.set_unusable_password()
            user.save(update_fields=["is_staff", "is_superuser", "password"])
        # **Only the role groups.** This was `groups.set([...])`, which also removed the `kc:/…`
        # groups `sync_user_roles` writes from the token at every request — so running the seed
        # silently un-granted every demo user's use-case access until their next request repaired
        # it. Touching what this contribution owns and nothing else is the fix.
        user.groups.remove(*role_groups)
        if held is not None:
            user.groups.add(groups[held])
        invited += int(_invite(user))

    return {
        "groups": len(Role),
        "groups_created": groups_created,
        "groups_retired": groups_retired,
        "users": len(DEMO_USERS),
        "users_created": users_created,
        "invited": invited,
    }


def _invite(user: Any) -> bool:
    """Make this seeded account claimable by whoever signs in under its name — **once**.

    The demo's whole point is that `ucadmin` in Keycloak and `ucadmin` here are the same person,
    and nothing else can say so: the realm is a fixture this seed does not read, and a `sub` it
    cannot know. So the account carries an invitation, and the first token bearing that
    `preferred_username` consumes it (`apps.api.models.PendingIdentity`).

    **Never for an account that has already been claimed.** Re-running the seed must not reopen a
    binding that exists — that would be the takeover this replaced, reissued by a maintenance
    command. The `OidcIdentity` is the evidence, and its absence is the condition.
    """
    if OidcIdentity.objects.filter(user=user).exists():
        return False
    _, created = PendingIdentity.objects.get_or_create(user=user)
    return bool(created)
