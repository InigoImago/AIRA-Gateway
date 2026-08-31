"""Granting access, and ending it — every route in and every route out (`FRD-613`, `FRD-209`).

Two halves of one lifecycle, and each had a hole the other made invisible.

**Getting in.** `FRD-209` FR-4 says the console searches groups *and users* and grants either. The
picker offered everybody the directory knows and the server resolved only people who had already
signed in, so granting a new colleague answered `Unknown user 'x'` — a control the console offers
and the server refuses, on the single route the person-grant half of the feature exists for.

**Getting out.** Removing somebody took away their console view, their object permissions and the
membership row the gateway reads, and left every **API key** they held for that use case active,
bound, and serving traffic against its budget until it happened to expire. Every consequence of the
removal was immediate except the one that actually reaches a model.

The third property here is the one that connects them: a key names an owner, and naming somebody
else transfers authority — so it is an administrator's act, and only somebody whose access can
*end* may be named.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.api.models import PendingIdentity
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import django_group_name, sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APIClient

from aira_common.access import SubjectKind
from aira_common.directory import DirectoryEntry, DirectoryUnavailable

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
GROUP_FOR = {
    "global-admin": "/aira/global-admins",
    "it-security": "/aira/it-security",
    "it-steuerung": "/aira/it-steuerung",
}


def _user(username: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"groups": [GROUP_FOR[role] for role in roles]})
    return get_user_model().objects.get(pk=user.pk)


def _member(username: str, usecase: UseCase, role: str = UseCaseMembership.USER) -> Any:
    user = _user(username)
    _grant(user, usecase, role)
    UseCaseMembership.objects.create(use_case=usecase, user=user, role=role)
    return get_user_model().objects.get(pk=user.pk)


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def usecase() -> UseCase:
    return UseCase.objects.create(slug="uc-a", name="A")


@pytest.fixture
def boss(usecase: UseCase) -> Any:
    return _member("boss", usecase, UseCaseMembership.ADMIN)


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def _key_for(usecase: UseCase, owner: Any, prefix: str = "aabbccdd") -> ApiKey:
    return ApiKey.objects.create(
        use_case=usecase, owner=owner, prefix=prefix, key_hash="0" * 64, label="k"
    )


# ═══ 1. getting in ══════════════════════════════════════════════════════════════════════════════


def test_a_member_who_has_signed_in_is_added_by_name(
    usecase: UseCase, boss: Any, captured_events: list[tuple[str, dict]]
) -> None:
    _user("ada")
    added = _client(boss).post(
        f"{BASE}uc-a/members/", {"username": "ada", "role": "user"}, format="json"
    )
    assert added.status_code == 201
    assert ("membership.upserted", {"slug": "uc-a", "username": "ada", "role": "user"}) in [
        (kind, payload) for kind, payload in captured_events
    ]


def test_somebody_the_directory_knows_can_be_granted_before_they_ever_sign_in(
    usecase: UseCase, boss: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FRD-209` FR-4, finally holding on the half it never did."""
    monkeypatch.setattr(
        "aira_management.apps.usecases.views.known_person",
        lambda username: DirectoryEntry(
            kind=SubjectKind.USER, id=username, label="Ada", detail="ada@example.org"
        ),
    )
    added = _client(boss).post(
        f"{BASE}uc-a/members/", {"username": "newcomer", "role": "admin"}, format="json"
    )
    assert added.status_code == 201
    created = get_user_model().objects.get(username="newcomer")
    assert created.email == "ada@example.org"
    assert PendingIdentity.objects.get(user=created).invited_by == "boss"


def test_a_name_the_directory_does_not_know_is_refused(
    usecase: UseCase, boss: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account created for a name nobody has is an accountability chain ending in a string."""
    monkeypatch.setattr("aira_management.apps.usecases.views.known_person", lambda username: None)
    refused = _client(boss).post(
        f"{BASE}uc-a/members/", {"username": "nobody", "role": "user"}, format="json"
    )
    assert refused.status_code == 400
    assert "directory knows no user" in refused.content.decode()
    assert not get_user_model().objects.filter(username="nobody").exists()


def test_a_directory_that_cannot_be_asked_says_so_rather_than_denying_the_person(
    usecase: UseCase, boss: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three outcomes, and they send three different people to fix them. "No such colleague" for a
    directory that is down would have somebody looking for a typo in a correct name."""

    def _unavailable(username: str) -> DirectoryEntry:
        raise DirectoryUnavailable("no directory client is configured")

    monkeypatch.setattr("aira_management.apps.usecases.views.known_person", _unavailable)
    refused = _client(boss).post(
        f"{BASE}uc-a/members/", {"username": "newcomer", "role": "user"}, format="json"
    )
    assert refused.status_code == 400
    body = refused.content.decode()
    assert "could not be asked" in body
    assert "sign in to the console once" in body


def test_only_an_administrator_of_the_use_case_may_add_members(usecase: UseCase) -> None:
    plain = _member("plain", usecase)
    _user("ada")
    refused = _client(plain).post(
        f"{BASE}uc-a/members/", {"username": "ada", "role": "user"}, format="json"
    )
    assert refused.status_code == 403


# ═══ 2. getting out ═════════════════════════════════════════════════════════════════════════════


def test_a_member_whose_name_contains_a_dot_can_be_removed(usecase: UseCase, boss: Any) -> None:
    """`first.last` is the single most ordinary shape a directory hands out, and the route's
    default pattern excluded a dot to keep `.json` suffixes routable — so removing them answered
    `404`, and percent-encoding does not help because the path is decoded before it is matched."""
    _member("vadim.scheibe", usecase)
    removed = _client(boss).delete(f"{BASE}uc-a/members/vadim.scheibe/")
    assert removed.status_code == 200
    assert not UseCaseMembership.objects.filter(user__username="vadim.scheibe").exists()


def test_removing_a_member_revokes_the_keys_that_rested_on_their_access(
    usecase: UseCase, boss: Any, captured_events: list[tuple[str, dict]]
) -> None:
    """The offboarding hole in its plainest form: measured on 2026-08-30, a removed member's key
    answered `200` on the surface they had just been removed from."""
    ada = _member("ada", usecase)
    key = _key_for(usecase, ada)

    removed = _client(boss).delete(f"{BASE}uc-a/members/ada/")

    assert removed.status_code == 200
    assert removed.json() == {"revoked_keys": [key.prefix]}
    key.refresh_from_db()
    assert not key.is_active
    assert key.revoked_at is not None
    assert ("api_key.revoked", {"prefix": key.prefix, "use_case": "uc-a", "status": "revoked"}) in [
        (kind, payload) for kind, payload in captured_events
    ]


def test_removing_one_member_leaves_everybody_elses_keys_alone(usecase: UseCase, boss: Any) -> None:
    ada = _member("ada", usecase)
    bob = _member("bob", usecase)
    ada_key = _key_for(usecase, ada, prefix="aaaa1111")
    bob_key = _key_for(usecase, bob, prefix="bbbb2222")

    _client(boss).delete(f"{BASE}uc-a/members/ada/")

    ada_key.refresh_from_db()
    bob_key.refresh_from_db()
    assert not ada_key.is_active
    assert bob_key.is_active


def test_a_key_in_another_use_case_is_untouched(usecase: UseCase, boss: Any) -> None:
    other = UseCase.objects.create(slug="uc-b", name="B")
    ada = _member("ada", usecase)
    _grant(ada, other, UseCaseMembership.USER)
    UseCaseMembership.objects.create(use_case=other, user=ada, role="user")
    elsewhere = _key_for(other, ada, prefix="cccc3333")

    _client(boss).delete(f"{BASE}uc-a/members/ada/")

    elsewhere.refresh_from_db()
    assert elsewhere.is_active


def test_revoking_a_group_grant_revokes_the_keys_it_was_holding_up(
    usecase: UseCase, boss: Any
) -> None:
    """Nobody was named at all, and a key's owner may have been reaching the use case only through
    that group."""
    ada = _user("ada")
    sync_user_groups(ada, {"groups": ["/ai/kundenservice"]})
    ada = get_user_model().objects.get(pk=ada.pk)
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/ai/kundenservice", role="user")
    group, _ = Group.objects.get_or_create(name=django_group_name("/ai/kundenservice"))
    _grant(group, usecase, UseCaseMembership.USER)
    key = _key_for(usecase, ada)

    revoked = _client(boss).delete(f"{BASE}uc-a/groups/revoke/?group_path=/ai/kundenservice")

    assert revoked.status_code == 200
    assert revoked.json() == {"revoked_keys": [key.prefix]}
    key.refresh_from_db()
    assert not key.is_active


def test_somebody_granted_twice_over_keeps_their_key_when_one_route_closes(
    usecase: UseCase, boss: Any
) -> None:
    """`FRD-209` FR-5 one layer down: revoking one route must not silently close another."""
    ada = _member("ada", usecase)
    sync_user_groups(ada, {"groups": ["/ai/kundenservice"]})
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/ai/kundenservice", role="user")
    key = _key_for(usecase, ada)

    revoked = _client(boss).delete(f"{BASE}uc-a/groups/revoke/?group_path=/ai/kundenservice")

    assert revoked.json() == {"revoked_keys": []}
    key.refresh_from_db()
    assert key.is_active


def test_an_already_revoked_key_is_not_revoked_twice(usecase: UseCase, boss: Any) -> None:
    """Revocation is terminal and dated. Re-stamping it would move the date of an event that
    happened once, and re-emit a decision the gateway has already applied."""
    ada = _member("ada", usecase)
    key = _key_for(usecase, ada)
    key.is_active = False
    key.save(update_fields=["is_active"])

    removed = _client(boss).delete(f"{BASE}uc-a/members/ada/")

    assert removed.json() == {"revoked_keys": []}


def test_removing_a_member_names_the_stored_username_on_the_event(
    usecase: UseCase, boss: Any, captured_events: list[tuple[str, dict]]
) -> None:
    """The consumer keys `use_case_members` on this string. One character different and the
    gateway keeps a membership Management has removed."""
    _member("vadim.scheibe", usecase)
    _client(boss).delete(f"{BASE}uc-a/members/vadim.scheibe/")
    assert ("membership.removed", {"slug": "uc-a", "username": "vadim.scheibe"}) in [
        (kind, payload) for kind, payload in captured_events
    ]


# ═══ 3. who a key may be owned by ═══════════════════════════════════════════════════════════════


def test_a_member_may_issue_a_key_for_themselves(usecase: UseCase) -> None:
    plain = _member("plain", usecase)
    issued = _client(plain).post(f"{BASE}uc-a/api-keys/", {"label": "mine"}, format="json")
    assert issued.status_code == 201
    assert issued.json()["owner"] == "plain"
    assert issued.json()["issued_by"] == ""


def test_a_plain_member_may_not_issue_a_key_owned_by_somebody_else(
    usecase: UseCase, boss: Any
) -> None:
    """The escalation this closed. A key acts with its owner's standing on the gateway's console
    endpoints, spends their allowance and carries their name in every audit row — so a use-case
    *user* naming the administrator read every stored prompt in a use case set to show each member
    their own, with the access recorded against the administrator."""
    plain = _member("plain", usecase)
    refused = _client(plain).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "boss"}, format="json"
    )
    assert refused.status_code == 403
    assert not ApiKey.objects.filter(owner__username="boss").exists()


def test_an_administrator_may_issue_a_key_for_a_technical_account(
    usecase: UseCase, boss: Any
) -> None:
    """The feature this is a control on, still working: a shared credential whose owner answers for
    it and whose issuer is the human who created it (`FRD-604` FR-5)."""
    _member("batch-account", usecase)
    issued = _client(boss).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "batch-account"}, format="json"
    )
    assert issued.status_code == 201
    assert issued.json()["owner"] == "batch-account"
    assert issued.json()["issued_by"] == "boss"


def test_a_key_cannot_be_owned_by_somebody_with_no_grant_on_the_use_case(
    usecase: UseCase, boss: Any
) -> None:
    _user("outsider")
    refused = _client(boss).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "outsider"}, format="json"
    )
    assert refused.status_code == 400
    assert "no access to this use case" in refused.content.decode()


def test_a_global_administrator_who_is_a_member_of_nothing_cannot_own_a_key(
    usecase: UseCase, boss: Any
) -> None:
    """`is_member` says yes to a Global Administrator everywhere, because in *Management* they may
    act anywhere. An owner has to be somebody whose access can **end** — otherwise the key rests on
    a blanket that never closes, and `_revoke_keys_without_access` could never revoke it."""
    _user("root", "global-admin")
    refused = _client(boss).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "root"}, format="json"
    )
    assert refused.status_code == 400


def test_naming_yourself_is_not_naming_somebody_else(usecase: UseCase) -> None:
    """Spelling out your own name must not require administering the use case, and must not put a
    distinction on the row that nobody asked for."""
    plain = _member("plain", usecase)
    issued = _client(plain).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "plain"}, format="json"
    )
    assert issued.status_code == 201
    assert issued.json()["issued_by"] == ""


def test_an_unknown_owner_is_refused_rather_than_created(usecase: UseCase, boss: Any) -> None:
    refused = _client(boss).post(
        f"{BASE}uc-a/api-keys/", {"label": "k", "owner": "ghost"}, format="json"
    )
    assert refused.status_code == 400
    assert not get_user_model().objects.filter(username="ghost").exists()
