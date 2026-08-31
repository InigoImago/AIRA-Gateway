from io import StringIO

import pytest
from aira_management.apps.seed.contributions.roles_and_users import (
    DEMO_USERS,
    seed_roles_and_users,
)
from aira_management.apps.seed.registry import contributions
from aira_management.config.app_settings import ManagementSettings
from aira_management.roles import Role
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def test_registry_contains_roles_and_users() -> None:
    names = [c.name for c in contributions()]
    assert "roles_and_users" in names
    # contributions are ordered
    orders = [c.order for c in contributions()]
    assert orders == sorted(orders)


def test_seed_creates_groups_and_users() -> None:
    summary = seed_roles_and_users(fresh=False)
    user_model = get_user_model()

    assert Group.objects.count() == len(Role)
    assert user_model.objects.count() == len(DEMO_USERS)
    assert summary["groups_created"] == len(Role)
    assert summary["users_created"] == len(DEMO_USERS)

    admin = user_model.objects.get(username="admin")
    # **Not a superuser, and not staff.** `may_admin`/`may_manage` ask `user.has_perm(…, usecase)`,
    # and Django answers True to a superuser before any backend runs — so the flag administered
    # every use case from a fact stored here rather than read from the directory, and removing the
    # role group in Keycloak took the role away and changed nothing (`FRD-613`).
    assert not admin.is_superuser
    assert not admin.is_staff
    assert not admin.has_usable_password()
    assert admin.groups.filter(name=str(Role.GLOBAL_ADMIN)).exists()

    # `ucuser` and `ucadmin` hold **no organisation-wide role**, and that is the point of them.
    # What they can do comes from a grant on a use case (`ADR-0017`, `FRD-209`) — so switching to
    # `ucadmin` in a walkthrough demonstrates the grant rather than a badge. They carried
    # `use-case-admin` and `use-case-user` until 2026-08-10, a day after those roles were
    # abolished, which made the demo argue for the mechanism it had replaced.
    ucuser = user_model.objects.get(username="ucuser")
    assert not ucuser.is_superuser
    assert not ucuser.groups.exists()


def test_the_seed_leaves_the_groups_it_does_not_own_alone() -> None:
    """Use-case access is synced onto Django groups from the token at every request
    (`FRD-209`). This contribution owns the *role* groups and nothing else — it used to
    `groups.set([...])`, which removed the synced ones, so running the seed silently un-granted
    every demo user until their next request repaired it."""
    from django.contrib.auth.models import Group

    seed_roles_and_users(fresh=False)
    user = get_user_model().objects.get(username="ucadmin")
    synced, _ = Group.objects.get_or_create(name="kc:/use-cases/kundenservice")
    user.groups.add(synced)

    seed_roles_and_users(fresh=False)

    assert user.groups.filter(name="kc:/use-cases/kundenservice").exists()


def test_seed_is_idempotent() -> None:
    seed_roles_and_users(fresh=False)
    second = seed_roles_and_users(fresh=False)
    user_model = get_user_model()

    assert user_model.objects.count() == len(DEMO_USERS)
    assert Group.objects.count() == len(Role)
    assert second["users_created"] == 0
    assert second["groups_created"] == 0


def test_seed_fresh_removes_stray_demo_users() -> None:
    seed_roles_and_users(fresh=False)
    user_model = get_user_model()
    user_model.objects.create(username="stray", email="stray@demo.aira")

    seed_roles_and_users(fresh=True)
    assert not user_model.objects.filter(username="stray").exists()
    assert user_model.objects.count() == len(DEMO_USERS)


def test_command_runs() -> None:
    out = StringIO()
    call_command("seed_demo", stdout=out)
    output = out.getvalue()
    assert "roles_and_users" in output
    assert "done" in output
    assert get_user_model().objects.count() == len(DEMO_USERS)


def test_command_refuses_production_without_force(monkeypatch) -> None:
    from aira_management.apps.seed.management.commands import seed_demo as cmd

    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(environment="production"))

    with pytest.raises(CommandError):
        call_command("seed_demo")

    # --force allows it
    call_command("seed_demo", "--force", stdout=StringIO())
    assert get_user_model().objects.count() == len(DEMO_USERS)


def test_the_seed_removes_the_groups_of_roles_that_no_longer_exist() -> None:
    """`use-case-admin` and `use-case-user` were roles until `ADR-0017`. The seed created a Django
    group for each, and those rows outlived the roles: assignable by hand, conferring nothing.

    Named explicitly rather than "anything the seed does not recognise" — that rule would take the
    `kc:/…` groups with it, which is where use-case access actually lives.
    """
    from django.contrib.auth.models import Group

    Group.objects.get_or_create(name="use-case-admin")
    Group.objects.get_or_create(name="use-case-user")
    survivor, _ = Group.objects.get_or_create(name="kc:/use-cases/kundenservice")

    seed_roles_and_users(fresh=False)

    assert not Group.objects.filter(name__in=["use-case-admin", "use-case-user"]).exists()
    assert Group.objects.filter(pk=survivor.pk).exists()


def test_re_seeding_takes_away_a_superuser_flag_an_older_seed_left_behind() -> None:
    """The seed **clears** the flags, it does not merely stop setting them (`FRD-613`).

    An installation that ran an older seed is carrying `is_superuser` on its `admin` account right
    now, and that flag is `ADR-0017` undone in one column: `may_admin` and `may_manage` ask
    `user.has_perm(…, usecase)`, Django answers True to a superuser before any backend runs, and
    guardian's `get_objects_for_user` short-circuits the same way. So the account administered
    every use case that would ever exist from a fact stored *here* — and removing the role group in
    Keycloak took the role away and changed nothing.

    A fix that only stops creating the flag leaves every existing installation exactly as it was,
    which is the half of a correction nobody notices is missing.
    """
    seed_roles_and_users(fresh=False)
    user_model = get_user_model()
    stale = user_model.objects.get(username="admin")
    stale.is_superuser = True
    stale.is_staff = True
    stale.set_password("demo-password")
    stale.save()

    seed_roles_and_users(fresh=False)

    repaired = user_model.objects.get(username="admin")
    assert not repaired.is_superuser
    assert not repaired.is_staff
    assert not repaired.has_usable_password()


def test_the_seed_invites_its_demo_people_and_does_not_reopen_a_claimed_one() -> None:
    """The demo's whole point is that `ucadmin` in Keycloak and `ucadmin` here are the same person,
    and nothing else can say so: the realm is a fixture this seed does not read, and a `sub` it
    cannot know. So the account carries an invitation — and re-running the seed must not reopen one
    that has already been claimed, which would be the takeover reissued by a maintenance command.
    """
    from aira_management.apps.api.models import OidcIdentity, PendingIdentity

    seed_roles_and_users(fresh=False)
    assert PendingIdentity.objects.count() == len(DEMO_USERS)

    claimed = get_user_model().objects.get(username="ucadmin")
    PendingIdentity.objects.filter(user=claimed).delete()
    OidcIdentity.objects.create(subject="sub-ucadmin", user=claimed)

    seed_roles_and_users(fresh=False)

    assert not PendingIdentity.objects.filter(user=claimed).exists()
