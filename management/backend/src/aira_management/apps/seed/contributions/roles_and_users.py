"""Phase 0 seed contribution: the five roles (as Groups) and one demo user each.

Deterministic and idempotent: users are keyed by a fixed username, groups by role name,
so re-running produces the same state. ``fresh`` removes existing demo users first.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from aira_management.apps.seed.registry import SeedResult, register
from aira_management.roles import Role

DEMO_DOMAIN = "demo.aira"
DEMO_PASSWORD = "demo-password"

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
    # `held` rather than `role`: the loop above binds `role` to a `Role`, and this one to a role
    # **or none**. Reusing the name made mypy right about something a reader would also trip on.
    for username, held in DEMO_USERS.items():
        is_admin = held is Role.GLOBAL_ADMIN
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@{DEMO_DOMAIN}",
                "is_staff": is_admin,
                "is_superuser": is_admin,
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            users_created += 1
        # **Only the role groups.** This was `groups.set([...])`, which also removed the `kc:/…`
        # groups `sync_user_roles` writes from the token at every request — so running the seed
        # silently un-granted every demo user's use-case access until their next request repaired
        # it. Touching what this contribution owns and nothing else is the fix.
        user.groups.remove(*role_groups)
        if held is not None:
            user.groups.add(groups[held])

    return {
        "groups": len(Role),
        "groups_created": groups_created,
        "groups_retired": groups_retired,
        "users": len(DEMO_USERS),
        "users_created": users_created,
    }
