"""API-key issuance/list/revoke (FRD-205)."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.config.runtime import get_settings
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from .conftest import role_claims


@contextmanager
def override_settings_value(**values: Any) -> Iterator[None]:
    """Temporarily change a settings value the *serializer* reads at request time.

    Django's `override_settings` cannot reach these: they live on the pydantic settings object, not
    in `django.conf.settings`. Restored on the way out so one test cannot decide another's policy.
    """
    settings = get_settings()
    previous = {key: getattr(settings, key) for key in values}
    for key, value in values.items():
        object.__setattr__(settings, key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_uc(admin, slug: str = "demo-uc") -> UseCase:
    _client(admin).post(BASE, {"slug": slug, "name": "Demo"}, format="json")
    return UseCase.objects.get(slug=slug)


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


# ---- issue ------------------------------------------------------------------------------


def test_issue_returns_plaintext_once_and_stores_only_hash() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(f"{BASE}demo-uc/api-keys/", {"label": "cli"}, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key"].startswith("aira_")
    assert body["use_case"] == "demo-uc"

    record = ApiKey.objects.get(prefix=body["prefix"])
    # Only the hash is stored — never the plaintext.
    assert record.key_hash == hashlib.sha256(body["api_key"].encode()).hexdigest()
    assert record.key_hash != body["api_key"]
    assert record.owner == admin
    assert record.is_active is True
    assert str(record) == f"{record.prefix} (demo-uc)"


def test_issue_emits_created_event_without_plaintext(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(f"{BASE}demo-uc/api-keys/", {"label": "cli"}, format="json")
    full = resp.json()["api_key"]

    created = [p for t, p in captured_events if t == "api_key.created"]
    assert len(created) == 1
    payload = created[0]
    assert payload["use_case"] == "demo-uc"
    assert payload["subject"] == "admin1"
    assert payload["status"] == "active"
    # The plaintext key must never appear in an event.
    assert "api_key" not in payload
    assert full not in payload.values()


def test_member_with_user_role_may_issue() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )

    resp = _client(member).post(f"{BASE}demo-uc/api-keys/", {}, format="json")
    assert resp.status_code == 201
    assert ApiKey.objects.get(prefix=resp.json()["prefix"]).owner == member


def test_issue_forbidden_for_non_member() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    # Not a member, and no organisation-wide role: a Global Administrator is a member of
    # everything (`access.is_member`), so one would not be an outsider at all.
    outsider = _user("eve")
    resp = _client(outsider).post(f"{BASE}demo-uc/api-keys/", {}, format="json")
    assert resp.status_code == 404


# ---- list -------------------------------------------------------------------------------


def test_list_keys_is_masked() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _client(admin).post(f"{BASE}demo-uc/api-keys/", {"label": "one"}, format="json")

    resp = _client(admin).get(f"{BASE}demo-uc/api-keys/")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert set(rows[0]) == {
        "prefix",
        "label",
        "owner",
        "is_active",
        "created_at",
        "revoked_at",
        "expires_at",
    }
    assert "key_hash" not in rows[0]
    assert "api_key" not in rows[0]


# ---- revoke -----------------------------------------------------------------------------


def test_revoke_deactivates_and_emits(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    prefix = _client(admin).post(f"{BASE}demo-uc/api-keys/", {}, format="json").json()["prefix"]

    resp = _client(admin).delete(f"{BASE}demo-uc/api-keys/{prefix}/")
    assert resp.status_code == 204

    record = ApiKey.objects.get(prefix=prefix)
    assert record.is_active is False
    assert record.revoked_at is not None
    revoked = [p for t, p in captured_events if t == "api_key.revoked"]
    assert revoked == [{"prefix": prefix, "use_case": "demo-uc", "status": "revoked"}]


def test_revoke_unknown_prefix_is_400() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    assert _client(admin).delete(f"{BASE}demo-uc/api-keys/nope/").status_code == 400


def test_revoke_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    prefix = _client(admin).post(f"{BASE}demo-uc/api-keys/", {}, format="json").json()["prefix"]
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )

    resp = _client(member).delete(f"{BASE}demo-uc/api-keys/{prefix}/")
    assert resp.status_code == 403
    assert ApiKey.objects.get(prefix=prefix).is_active is True


# ---- expiry (2026-08-08) --------------------------------------------------------------------
#
# **A key is always bounded.** The first version of this made the expiry optional with "NULL means
# never", on the argument that an expiry which cannot be omitted is one somebody sets to the year
# 3000. That argument is about the *maximum*, not the default — the answer is a bound at both ends.
# A credential with no end date has to be inventoried by a person who remembers to, and nobody does.


def test_a_key_issued_without_asking_still_gets_the_default_lifetime() -> None:
    """The case that matters: nobody has to remember. Omitting the field is the common path, so it
    is the one that must not produce an unbounded credential."""
    admin = _user("exp-admin1", "global-admin")
    _make_uc(admin, "exp-uc-1")

    resp = _client(admin).post(f"{BASE}exp-uc-1/api-keys/", {"label": "one"}, format="json")

    assert resp.status_code == 201
    expires = datetime.fromisoformat(resp.json()["expires_at"])
    days = (expires - timezone.now()).days
    assert days == get_settings().api_key_default_days - 1  # a fraction of the first day is gone


def test_the_default_comes_from_configuration() -> None:
    """A policy nobody can change is one an installation works around."""
    admin = _user("exp-admin4", "global-admin")
    _make_uc(admin, "exp-uc-4")

    with override_settings_value(api_key_default_days=7):
        resp = _client(admin).post(f"{BASE}exp-uc-4/api-keys/", {"label": "one"}, format="json")

    expires = datetime.fromisoformat(resp.json()["expires_at"])
    assert (expires - timezone.now()).days == 6


def test_a_lifetime_past_the_maximum_is_refused_by_name() -> None:
    """Refused, not silently truncated: a shortened lifetime would leave the requester believing a
    date that is not the one in the database."""
    admin = _user("exp-admin5", "global-admin")
    _make_uc(admin, "exp-uc-5")

    resp = _client(admin).post(
        f"{BASE}exp-uc-5/api-keys/", {"label": "one", "expires_in_days": 3650}, format="json"
    )

    assert resp.status_code == 400
    assert "180" in str(resp.json())


def test_the_maximum_itself_is_allowed() -> None:
    """An off-by-one here would make the documented ceiling unreachable."""
    admin = _user("exp-admin6", "global-admin")
    _make_uc(admin, "exp-uc-6")

    resp = _client(admin).post(
        f"{BASE}exp-uc-6/api-keys/",
        {"label": "one", "expires_in_days": get_settings().api_key_max_days},
        format="json",
    )

    assert resp.status_code == 201


def test_there_is_no_way_to_ask_for_a_key_that_never_expires() -> None:
    """`null` is the shape a client would reach for. It takes the default rather than meaning
    "forever" — the whole point of the bound."""
    admin = _user("exp-admin7", "global-admin")
    _make_uc(admin, "exp-uc-7")

    resp = _client(admin).post(
        f"{BASE}exp-uc-7/api-keys/", {"label": "one", "expires_in_days": None}, format="json"
    )

    assert resp.status_code == 201
    assert resp.json()["expires_at"] is not None


def test_an_expiry_is_recorded_and_published() -> None:
    admin = _user("exp-admin2", "global-admin")
    _make_uc(admin, "exp-uc-2")

    resp = _client(admin).post(
        f"{BASE}exp-uc-2/api-keys/", {"label": "one", "expires_in_days": 30}, format="json"
    )

    assert resp.status_code == 201
    assert resp.json()["expires_at"] is not None
    key = ApiKey.objects.get(prefix=resp.json()["prefix"])
    assert key.expires_at is not None
    event = OutboxEvent.objects.filter(event_type="api_key.created").latest("id")
    # The gateway enforces it, so the date has to survive the wire — a column Management fills and
    # nothing carries across is the shape this project has now recorded three times.
    assert event.payload["expires_at"] == key.expires_at.isoformat()


def test_a_nonsensical_lifetime_is_refused() -> None:
    admin = _user("exp-admin3", "global-admin")
    _make_uc(admin, "exp-uc-3")

    resp = _client(admin).post(
        f"{BASE}exp-uc-3/api-keys/", {"label": "one", "expires_in_days": 0}, format="json"
    )

    assert resp.status_code == 400
