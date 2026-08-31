"""Who a token becomes, and what it can never become (`FRD-613`).

Every request to this plane provisions or resolves a Django user from a verified token, and that
user carries object permissions, memberships and API keys. So the question *"which account does
this `sub` get"* is the whole access-control surface of Management wearing a different hat, and
until the round that produced this file it had an answer nobody had written down: **any unbound
account whose username matched**.

The rules asserted here:

- a `sub` that has been seen before gets its own account, always;
- a `sub` that has not may claim an account **only where somebody invited it**, and only once;
- everything else gets a fresh account, and a name already taken never carries authority with it;
- nothing a directory can put in a claim reaches a column it does not fit, and nothing about a
  claim can make a first sign-in fail.

The last is not defensive programming for its own sake: `username` is `varchar(150)` and `email`
is `varchar(254)`, SQLite enforces neither, and the hermetic suite runs on SQLite. A `DataError`
here is a **500 on somebody's first sign-in**, on a value they do not control.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.api.authentication import (
    EMAIL_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    KeycloakJWTAuthentication,
    safe_email,
    safe_username,
)
from aira_management.apps.api.models import OidcIdentity, PendingIdentity
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from guardian.shortcuts import get_objects_for_user

pytestmark = pytest.mark.django_db


def _provision(subject: str, **claims: Any) -> Any:
    return KeycloakJWTAuthentication._provision_user(subject, claims)


# ═══ 1. the binding ═════════════════════════════════════════════════════════════════════════════


def test_a_new_subject_gets_an_account_named_after_them() -> None:
    user = _provision("sub-1", preferred_username="ada", email="ada@example.org")
    assert user.get_username() == "ada"
    assert user.email == "ada@example.org"
    assert OidcIdentity.objects.get(subject="sub-1").user_id == user.pk


def test_the_same_subject_comes_back_to_the_same_account() -> None:
    first = _provision("sub-1", preferred_username="ada")
    second = _provision("sub-1", preferred_username="ada")
    assert first.pk == second.pk
    assert OidcIdentity.objects.filter(subject="sub-1").count() == 1


def test_a_renamed_person_keeps_their_account() -> None:
    """The whole reason the binding is on `sub`: a username can be changed in the directory, and
    a person who changes theirs is the same person with the same memberships."""
    before = _provision("sub-1", preferred_username="ada")
    after = _provision("sub-1", preferred_username="ada.lovelace")
    assert after.pk == before.pk
    assert after.get_username() == "ada", (
        "the local name is descriptive; the binding is the subject, and renaming the account "
        "would move every membership keyed on the old name"
    )


def test_a_reused_username_never_inherits_the_previous_account() -> None:
    """A username freed by somebody leaving can be handed to somebody else. Keying on it would
    hand the new person the previous holder's object permissions (`ADR-0007`)."""
    original = _provision("sub-old", preferred_username="jdoe")
    newcomer = _provision("sub-new", preferred_username="jdoe")
    assert newcomer.pk != original.pk
    assert newcomer.get_username() != "jdoe"
    assert newcomer.get_username().startswith("jdoe-")


def test_a_reused_username_inherits_no_permission() -> None:
    """The consequence, asserted rather than inferred from the two rows being different."""
    usecase = UseCase.objects.create(slug="uc-a", name="A")
    original = _provision("sub-old", preferred_username="jdoe")
    _grant(original, usecase, UseCaseMembership.ADMIN)

    newcomer = _provision("sub-new", preferred_username="jdoe")

    assert not get_objects_for_user(newcomer, "usecases.manage_members", klass=UseCase).exists()


# ═══ 2. the invitation ══════════════════════════════════════════════════════════════════════════


def test_an_invited_account_is_claimed_by_the_name_it_was_invited_under() -> None:
    invited = get_user_model().objects.create(username="newcomer")
    PendingIdentity.objects.create(user=invited, invited_by="boss")

    claimed = _provision("sub-newcomer", preferred_username="newcomer")

    assert claimed.pk == invited.pk
    assert OidcIdentity.objects.get(subject="sub-newcomer").user_id == invited.pk


def test_an_invitation_is_consumed_by_the_first_person_to_use_it() -> None:
    invited = get_user_model().objects.create(username="newcomer")
    PendingIdentity.objects.create(user=invited)

    first = _provision("sub-a", preferred_username="newcomer")
    second = _provision("sub-b", preferred_username="newcomer")

    assert second.pk != first.pk
    assert not PendingIdentity.objects.exists()


def test_an_uninvited_account_is_claimed_by_nobody() -> None:
    """The takeover this replaced. Measured on 2026-08-30: a token with an arbitrary `sub` and
    `preferred_username: "admin"` was handed the seeded `admin` account, its memberships and its
    object permissions — a claim available to whoever asked first, recorded nowhere."""
    theirs = get_user_model().objects.create(username="admin")
    stranger = _provision("sub-stranger", preferred_username="admin")
    assert stranger.pk != theirs.pk


def test_claiming_an_invited_account_keeps_the_access_it_was_given() -> None:
    """The point of an invitation: an administrator grants access **before** the colleague has
    ever signed in, and it is waiting for them when they do."""
    usecase = UseCase.objects.create(slug="uc-a", name="A")
    invited = get_user_model().objects.create(username="newcomer")
    PendingIdentity.objects.create(user=invited, invited_by="boss")
    UseCaseMembership.objects.create(use_case=usecase, user=invited, role="user")
    _grant(invited, usecase, UseCaseMembership.USER)

    claimed = _provision("sub-newcomer", preferred_username="newcomer")

    assert get_objects_for_user(claimed, "usecases.view_usecase", klass=UseCase).count() == 1


def test_an_invitation_for_one_name_does_not_answer_another() -> None:
    invited = get_user_model().objects.create(username="newcomer")
    PendingIdentity.objects.create(user=invited)
    other = _provision("sub-x", preferred_username="somebody-else")
    assert other.pk != invited.pk
    assert PendingIdentity.objects.filter(user=invited).exists()


# ═══ 3. what a claim may contain ════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        ("ada", "ada"),
        ("  ada  ", "ada"),
        ("ada.lovelace", "ada.lovelace"),
        ("ada+work@example.org", "ada+work@example.org"),
        ("ada-1_2", "ada-1_2"),
        ("", "sub-9"),
        ("   ", "sub-9"),
        (None, "sub-9"),
        (12345, "sub-9"),
        (["ada"], "sub-9"),
        ("a" * 151, "sub-9"),
        ("ada/lovelace", "sub-9"),
        ("ada lovelace", "sub-9"),
        ("ada\nlovelace", "sub-9"),
    ],
    ids=[
        "name",
        "padded",
        "with-a-dot",
        "email-shaped",
        "punctuation-django-allows",
        "empty",
        "blank",
        "absent",
        "number",
        "list",
        "too-long",
        "with-a-slash",
        "with-a-space",
        "with-a-newline",
    ],
)
def test_the_username_a_claim_can_produce(claimed: object, expected: str) -> None:
    """Three things a directory may hand over that this column cannot take — too long, characters
    Django's own validator refuses, and nothing at all — and all three arrive on the **first**
    request of somebody who has done nothing wrong.

    The fallback is the subject rather than a mangled name: silently rewriting somebody's name
    produces an account nobody can search for, where a subject says plainly that the directory gave
    us nothing usable. A `/` matters twice over — it would also make the member-removal route
    unable to address them.
    """
    assert safe_username(claimed, "sub-9") == expected


def test_a_subject_too_long_for_the_column_is_cut_rather_than_refused() -> None:
    assert len(safe_username(None, "s" * 400)) == USERNAME_MAX_LENGTH


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        ("ada@example.org", "ada@example.org"),
        ("  ada@example.org ", "ada@example.org"),
        ("", ""),
        (None, ""),
        (12345, ""),
        ({"address": "a"}, ""),
    ],
    ids=["address", "padded", "empty", "absent", "number", "object"],
)
def test_the_email_a_claim_can_produce(claimed: object, expected: str) -> None:
    assert safe_email(claimed) == expected


def test_a_long_address_is_cut_to_the_column() -> None:
    assert len(safe_email("a" * 300)) == EMAIL_MAX_LENGTH


def test_a_first_sign_in_survives_a_claim_the_columns_cannot_take() -> None:
    """The whole point of the two functions above, asserted through the door they guard rather
    than on them: on Postgres this was a `DataError` from the driver — a 500 on somebody's first
    request — and on SQLite it silently created a 400-character username."""
    user = _provision("sub-1", preferred_username="x" * 400, email="y" * 400)
    assert len(user.get_username()) <= USERNAME_MAX_LENGTH
    assert len(user.email) <= EMAIL_MAX_LENGTH


def test_a_suffixed_username_still_fits_the_column() -> None:
    """A name already at the limit, taken by somebody else. Appending a suffix past the column is
    the very `DataError` the bound exists to prevent."""
    long_name = "a" * USERNAME_MAX_LENGTH
    _provision("sub-old", preferred_username=long_name)
    newcomer = _provision("sub-new", preferred_username=long_name)
    assert len(newcomer.get_username()) <= USERNAME_MAX_LENGTH
    assert newcomer.get_username() != long_name


def test_two_names_differing_only_in_case_are_two_people() -> None:
    """Django's username lookup is case-sensitive on Postgres, and Keycloak's is not by default.

    Asserted rather than corrected: folding case here would be a **second** answer to "who is
    this" beside the `sub` binding — the mechanism `ADR-0017` abolished roles to avoid — and it
    would merge two real directory accounts if a realm ever allowed both. What matters is that
    neither inherits the other, which is what the binding already guarantees.
    """
    lower = _provision("sub-1", preferred_username="ada")
    upper = _provision("sub-2", preferred_username="Ada")
    assert lower.pk != upper.pk


# ═══ 4. what the account itself may not carry ═══════════════════════════════════════════════════


def test_a_provisioned_account_has_no_usable_password() -> None:
    """There is no password login on this API — no session authentication, no admin site — so a
    usable password would be a credential kept against the day somebody adds the door it opens."""
    user = _provision("sub-1", preferred_username="ada")
    assert not user.has_usable_password()


def test_a_provisioned_account_is_neither_staff_nor_superuser() -> None:
    """`is_superuser` is `ADR-0017` undone in one column: `may_admin` and `may_manage` ask
    `user.has_perm(…, usecase)`, and Django answers True to a superuser before any backend runs."""
    user = _provision("sub-1", preferred_username="ada")
    assert not user.is_staff
    assert not user.is_superuser


def test_authority_is_re_read_from_the_token_on_every_request() -> None:
    """Somebody removed from a group in the directory loses the role on their next token, without
    anything here being told."""
    user = _provision("sub-1", preferred_username="ada")
    sync_user_roles(user, {"groups": ["/aira/global-admins"]})
    assert "global-admin" in set(user.groups.values_list("name", flat=True))

    sync_user_roles(user, {"groups": []})
    assert "global-admin" not in set(user.groups.values_list("name", flat=True))


def test_group_membership_is_re_read_the_same_way() -> None:
    user = _provision("sub-1", preferred_username="ada")
    sync_user_groups(user, {"groups": ["/ai/kundenservice"]})
    assert "kc:/ai/kundenservice" in set(user.groups.values_list("name", flat=True))

    sync_user_groups(user, {"groups": []})
    assert not user.groups.filter(name__startswith="kc:").exists()


def test_a_realm_group_named_after_a_role_confers_nothing() -> None:
    """Mirror groups are prefixed so a realm with a group literally called `it-security` cannot
    hand out the role by accident."""
    user = _provision("sub-1", preferred_username="ada")
    sync_user_groups(user, {"groups": ["/it-security"]})
    from aira_management.rbac import role_slugs

    assert role_slugs(user) == set()
