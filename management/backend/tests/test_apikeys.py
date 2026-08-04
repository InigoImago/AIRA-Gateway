"""API-key issuance/list/revoke (FRD-205)."""

import hashlib

import pytest
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"realm_access": {"roles": list(roles)}})
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
    admin = _user("admin1", "use-case-admin")
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
    admin = _user("admin1", "use-case-admin")
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
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )

    resp = _client(member).post(f"{BASE}demo-uc/api-keys/", {}, format="json")
    assert resp.status_code == 201
    assert ApiKey.objects.get(prefix=resp.json()["prefix"]).owner == member


def test_issue_forbidden_for_non_member() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    outsider = _user("eve", "use-case-admin")  # has the role but is not a member
    resp = _client(outsider).post(f"{BASE}demo-uc/api-keys/", {}, format="json")
    assert resp.status_code == 404


# ---- list -------------------------------------------------------------------------------


def test_list_keys_is_masked() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    _client(admin).post(f"{BASE}demo-uc/api-keys/", {"label": "one"}, format="json")

    resp = _client(admin).get(f"{BASE}demo-uc/api-keys/")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert set(rows[0]) == {"prefix", "label", "owner", "is_active", "created_at", "revoked_at"}
    assert "key_hash" not in rows[0]
    assert "api_key" not in rows[0]


# ---- revoke -----------------------------------------------------------------------------


def test_revoke_deactivates_and_emits(captured_events) -> None:
    admin = _user("admin1", "use-case-admin")
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
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    assert _client(admin).delete(f"{BASE}demo-uc/api-keys/nope/").status_code == 400


def test_revoke_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    prefix = _client(admin).post(f"{BASE}demo-uc/api-keys/", {}, format="json").json()["prefix"]
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )

    resp = _client(member).delete(f"{BASE}demo-uc/api-keys/{prefix}/")
    assert resp.status_code == 403
    assert ApiKey.objects.get(prefix=prefix).is_active is True
