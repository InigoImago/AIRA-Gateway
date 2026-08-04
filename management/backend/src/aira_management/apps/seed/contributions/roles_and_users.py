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

# Deterministic demo username per role.
DEMO_USERS: dict[Role, str] = {
    Role.GLOBAL_ADMIN: "admin",
    Role.IT_SECURITY: "itsec",
    Role.IT_STEUERUNG: "itgov",
    Role.USE_CASE_ADMIN: "ucadmin",
    Role.USE_CASE_USER: "ucuser",
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

    users_created = 0
    for role, username in DEMO_USERS.items():
        is_admin = role is Role.GLOBAL_ADMIN
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
        user.groups.set([groups[role]])

    return {
        "groups": len(Role),
        "groups_created": groups_created,
        "users": len(DEMO_USERS),
        "users_created": users_created,
    }
