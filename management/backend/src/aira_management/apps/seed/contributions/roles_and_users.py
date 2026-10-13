"""Phase 0 seed contribution: the role groups and the five demo users.

Deterministic and idempotent: users are keyed by a fixed username, groups by role name,
so re-running produces the same state. ``fresh`` removes existing demo users first — except a
person named in the question catalogue's evidence, who is kept and made claimable again.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from aira_management.apps.api.models import OidcIdentity, PendingIdentity
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.seed.registry import SeedResult, register
from aira_management.apps.smoketests.models import TestResult, TestRun
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCaseMembership
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

    kept = _remove_demo_people() if fresh else 0

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
        "users_kept": kept,
        "invited": invited,
    }


def _remove_demo_people() -> int:
    """``--fresh``: delete the demo-domain accounts, announcing the access each one takes with it.

    A deletion cascades a person's keys and memberships here, and the gateway hears of neither
    unless it is told — a removed key would go on authenticating. Every key is announced revoked
    **except a demo key the showcase reissues** under the same prefix later in this run:
    revocation is terminal at the gateway, and the reissued key would answer 401 for ever.

    **A person named in the catalogue's evidence is kept** — who requested a run (`PROTECT`, so
    deleting them would abort the reset) or rated an answer (a verdict naming nobody is weaker
    evidence). Their sign-in binding is released, which is what the deletion was for: the demo
    person can claim the account again, from whatever subject the realm now gives them.

    Returns how many were kept, which the summary says.
    """
    # Imported here: the showcase is a later contribution, and this asks it a question only a
    # reset needs.
    from aira_management.apps.seed.contributions.showcase import reissued_prefixes
    from aira_management.apps.usecases.views.grants import _revoke_key

    reissued = reissued_prefixes()
    kept = 0
    for person in get_user_model().objects.filter(email__endswith=f"@{DEMO_DOMAIN}"):
        if (
            TestRun.objects.filter(requested_by=person).exists()
            or TestResult.objects.filter(rated_by=person).exists()
        ):
            OidcIdentity.objects.filter(user=person).delete()
            kept += 1
            continue
        username = person.get_username()
        with transaction.atomic():
            for key in ApiKey.objects.filter(owner=person, is_active=True).select_related(
                "use_case"
            ):
                if key.prefix not in reissued:
                    _revoke_key(key, key.use_case.slug)
            for membership in UseCaseMembership.objects.filter(user=person).select_related(
                "use_case"
            ):
                events.emit(
                    "membership.removed",
                    {"slug": membership.use_case.slug, "username": username},
                )
            person.delete()
    return kept


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
