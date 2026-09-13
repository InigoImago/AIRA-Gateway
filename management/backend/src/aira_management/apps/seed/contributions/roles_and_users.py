"""Phase 0 seed contribution: the role groups and the five demo users.

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
#: What `ucadmin` and `ucuser` may do comes from grants on individual use cases (`ADR-0017`,
#: `FRD-209`), which is what switching to them in a walkthrough shows.
DEMO_USERS: dict[str, Role | None] = {
    "admin": Role.GLOBAL_ADMIN,
    "itsec": Role.IT_SECURITY,
    "itgov": Role.IT_STEUERUNG,
    "ucadmin": None,
    "ucuser": None,
}

#: Groups for the roles `ADR-0017` abolished, which this contribution once created and so removes.
#: Named explicitly: removing whatever is not a `Role` would take the `kc:/…` groups access is
#: granted through.
RETIRED_ROLE_GROUPS = ["use-case-admin", "use-case-user"]


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

    retired = Group.objects.filter(name__in=RETIRED_ROLE_GROUPS)
    groups_retired = retired.count()
    retired.delete()

    role_groups = list(groups.values())

    users_created = 0
    invited = 0
    for username, held in DEMO_USERS.items():
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@{DEMO_DOMAIN}"},
        )
        if created:
            users_created += 1
        # **No `is_superuser`, no `is_staff`, no usable password** — cleared on every run, because
        # an older seed set all three. A superuser passes every `has_perm` before any backend is
        # asked, which would make `admin`'s reach a fact stored here rather than read from the
        # directory (`ADR-0017`); and a known password on the most privileged account waits for
        # the day somebody adds a login. Console sign-in is Keycloak's.
        stale = [field for field in ("is_staff", "is_superuser") if getattr(user, field, False)]
        if stale or user.has_usable_password():
            user.is_staff = False
            user.is_superuser = False
            user.set_unusable_password()
            user.save(update_fields=["is_staff", "is_superuser", "password"])
        # **Only the role groups**: `sync_user_roles` writes the `kc:/…` groups from the token, and
        # replacing those would un-grant the user's use-case access until their next request.
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

    Only the name links `ucadmin` in Keycloak to `ucadmin` here, so the account carries an
    invitation that the first token bearing that `preferred_username` consumes
    (`apps.api.models.PendingIdentity`). **Never for an account already claimed**: re-seeding must
    not reopen a binding; the `OidcIdentity` is the evidence.
    """
    if OidcIdentity.objects.filter(user=user).exists():
        return False
    _, created = PendingIdentity.objects.get_or_create(user=user)
    return bool(created)
