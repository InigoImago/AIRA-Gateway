from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO

import pytest
from aira_management.apps.seed.contributions.roles_and_users import (
    DEMO_USERS,
    seed_roles_and_users,
)
from aira_management.apps.seed.registry import contributions
from aira_management.apps.usecases import events
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


# == `--fresh` resets the demo and nothing the console made of it ================================


@contextmanager
def _recording() -> Iterator[list[tuple[str, dict]]]:
    """Every event emitted while the block runs — unsubscribed afterwards, because the subscriber
    list is module-global and holds the outbox's own subscriber."""
    seen: list[tuple[str, dict]] = []
    spy = events.subscribe(lambda kind, payload: seen.append((kind, payload)))
    try:
        yield seen
    finally:
        events.unsubscribe(spy)


def test_a_fresh_seed_survives_a_demo_person_who_ran_the_catalogue() -> None:
    """`TestRun.requested_by` is `PROTECT` on purpose — a run is evidence of who asked. Deleting the
    demo people therefore aborted the whole reset with `ProtectedError` once anybody had run the
    catalogue, which is the first thing a walkthrough of the smoke tests does. The person stays,
    their runs stay, and the sign-in binding a delete would have released is released."""
    from aira_management.apps.api.models import OidcIdentity, PendingIdentity
    from aira_management.apps.smoketests.models import TestRun

    call_command("seed_demo", stdout=StringIO())
    itsec = get_user_model().objects.get(username="itsec")
    OidcIdentity.objects.create(subject="sub-itsec-old-realm", user=itsec)
    PendingIdentity.objects.filter(user=itsec).delete()
    run = TestRun.objects.create(model="m", use_case="smoke-test", requested_by=itsec)

    out = StringIO()
    call_command("seed_demo", "--fresh", stdout=out)

    assert TestRun.objects.filter(pk=run.pk, requested_by__username="itsec").exists()
    assert not OidcIdentity.objects.filter(user=itsec).exists()
    assert PendingIdentity.objects.filter(user=itsec).exists(), "claimable again, like the others"
    assert "users_kept': 1" in out.getvalue()


def test_a_fresh_seed_never_retires_a_use_case_it_recreates() -> None:
    """The gateway keeps a retired use case as a tombstone, and an upsert never revives one. So a
    reset that announced `usecase.deleted` for a slug *another* contribution then recreated left
    that use case dead at the gateway and live in the console — `smoke-test`, every evaluation
    request refused with "has been retired". The slugs a reset keeps are every contribution's."""
    call_command("seed_demo", stdout=StringIO())

    with _recording() as seen:
        call_command("seed_demo", "--fresh", stdout=StringIO())

    retired = {payload["slug"] for kind, payload in seen if kind == "usecase.deleted"}
    recreated = {payload["slug"] for kind, payload in seen if kind == "usecase.upserted"}
    assert "smoke-test" in recreated
    assert not (retired & recreated), f"reset announced as retirement for {retired & recreated}"


def test_a_fresh_seed_announces_the_access_it_removes_and_spares_the_keys_it_reissues() -> None:
    """Deleting a person cascades their keys and memberships here and announced nothing, so the
    gateway kept a removed key authenticating and a removed membership granting. Each is announced
    now — except a demo key the showcase reissues under the same prefix a moment later: revocation
    is terminal at the gateway, and announcing one would kill the deterministic demo key for
    ever."""
    from aira_management.apps.apikeys.models import ApiKey
    from aira_management.apps.seed.contributions.showcase import reissued_prefixes
    from aira_management.apps.usecases.models import UseCase, UseCaseMembership

    call_command("seed_demo", stdout=StringIO())
    ucuser = get_user_model().objects.get(username="ucuser")
    personalwesen = UseCase.objects.get(slug="personalwesen")
    UseCaseMembership.objects.create(use_case=personalwesen, user=ucuser)
    ApiKey.objects.create(
        use_case=UseCase.objects.get(slug="kundenservice"),
        owner=ucuser,
        prefix="c0ffee00",
        key_hash="0" * 64,
        label="issued in the console",
    )

    with _recording() as seen:
        call_command("seed_demo", "--fresh", stdout=StringIO())

    revoked = {payload["prefix"] for kind, payload in seen if kind == "api_key.revoked"}
    assert "c0ffee00" in revoked
    assert not (revoked & reissued_prefixes()), "a reissued demo key must never be revoked"
    assert ("membership.removed", {"slug": "personalwesen", "username": "ucuser"}) in seen
    # And the demo's own keys still stand, reissued.
    assert reissued_prefixes() <= set(
        ApiKey.objects.filter(is_active=True).values_list("prefix", flat=True)
    )


# == the question catalogue belongs to IT Security once seeded ===================================


def test_re_seeding_leaves_the_catalogue_as_it_security_left_it() -> None:
    """Seeded, and then owned by IT Security in the console. Every seed run undid that: it rewrote
    the wording at each position, brought back a question at any position IT Security had retired,
    and retired every question the console had added. It now only fills positions nobody has ever
    used, live or retired."""
    from aira_management.apps.seed.contributions.test_catalogue import seed_test_catalogue
    from aira_management.apps.seed.contributions.test_catalogue_data import QUESTIONS
    from aira_management.apps.smoketests.models import TestCase as Case

    seed_test_catalogue(fresh=False)
    Case.objects.filter(position=7).update(retired=True)
    Case.objects.filter(position=5).update(prompt="Reworded by IT Security.")
    added = Case.objects.create(topic="Ours", prompt="Added in the console.", position=101)

    summary = seed_test_catalogue(fresh=False)

    assert summary["questions"] == 0
    assert not Case.objects.filter(position=7, retired=False).exists(), "retired stays retired"
    assert Case.objects.get(position=5).prompt == "Reworded by IT Security."
    assert Case.objects.get(pk=added.pk).retired is False
    assert Case.objects.filter(retired=False).count() == len(QUESTIONS)


def test_a_seed_fills_only_positions_never_used() -> None:
    """The comparison: a position nobody has used is still filled, so a catalogue seeded into an
    installation where IT Security started first gets the rest of the standard."""
    from aira_management.apps.seed.contributions.test_catalogue import seed_test_catalogue
    from aira_management.apps.seed.contributions.test_catalogue_data import QUESTIONS
    from aira_management.apps.smoketests.models import TestCase as Case

    Case.objects.create(topic="Theirs", prompt="Written before the seed ran.", position=1)

    summary = seed_test_catalogue(fresh=False)

    assert summary["questions"] == len(QUESTIONS) - 1
    assert Case.objects.get(position=1).topic == "Theirs"
    assert Case.objects.filter(retired=False).count() == len(QUESTIONS)


@pytest.mark.django_db
def test_the_showcase_stores_every_member_where_the_data_mode_is_shown() -> None:
    """`kundenservice` stores the most open setting on purpose (`FRD-627`): strict data mode reads
    it as oversight-only, and `make showcase-strict-off` shows what it means — so the same seed
    demonstrates both. A seed that left the default would demonstrate one, by accident."""
    from aira_management.apps.usecases.models import UseCase

    call_command("seed_demo", stdout=StringIO())

    assert UseCase.objects.get(slug="kundenservice").payload_visibility == "members"
