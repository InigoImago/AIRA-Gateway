"""Budget CRUD + distribution (FRD-400)."""

import pytest
from aira_management.apps.budgets.models import Budget
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


def test_create_use_case_budget_and_emit(captured_events) -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 100000},
        format="json",
    )
    assert resp.status_code == 201
    budget = Budget.objects.get(use_case__slug="demo-uc", scope="use_case")
    assert budget.limit_tokens == 100000
    published = [p for t, p in captured_events if t == "budget.upserted"]
    assert published[0]["use_case"] == "demo-uc"
    assert published[0]["id"] == budget.pk
    assert published[0]["limit_tokens"] == 100000


def test_member_budget_requires_subject() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "member", "period": "day", "limit_requests": 10},
        format="json",
    )
    assert resp.status_code == 400


def test_member_budget_created_with_subject() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "member", "subject": "bob", "period": "day", "limit_requests": 10},
        format="json",
    )
    assert resp.status_code == 201
    assert Budget.objects.get(use_case__slug="demo-uc", subject="bob").limit_requests == 10


def test_budget_requires_a_limit() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month"},
        format="json",
    )
    assert resp.status_code == 400


def test_budget_rejects_negative_limit() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_requests": -5},
        format="json",
    )
    assert resp.status_code == 400


def test_post_upserts_same_scope_period() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    client = _client(admin)
    client.post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    client.post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 2000},
        format="json",
    )
    budgets = Budget.objects.filter(use_case__slug="demo-uc", scope="use_case", period="month")
    assert budgets.count() == 1
    assert budgets.first().limit_tokens == 2000


def test_member_may_read_budgets() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    assert _client(member).get(f"{BASE}demo-uc/budgets/").status_code == 200


def test_delete_budget_emits(captured_events) -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    created = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    budget_id = created.json()["id"]

    resp = _client(admin).delete(f"{BASE}demo-uc/budgets/{budget_id}/")
    assert resp.status_code == 204
    assert not Budget.objects.filter(pk=budget_id).exists()
    assert ("budget.deleted", {"id": budget_id, "use_case": "demo-uc"}) in captured_events


def test_delete_unknown_budget_is_400() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    assert _client(admin).delete(f"{BASE}demo-uc/budgets/999/").status_code == 400


def test_delete_budget_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    created = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).delete(f"{BASE}demo-uc/budgets/{created.json()['id']}/")
    assert resp.status_code == 403


def test_budget_write_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    assert resp.status_code == 403


def test_str_representation() -> None:
    admin = _user("admin1", "use-case-admin")
    uc = _make_uc(admin, "demo-uc")
    budget = Budget.objects.create(use_case=uc, scope="member", subject="bob", period="day")
    assert str(budget) == "budget member:bob (day)"
