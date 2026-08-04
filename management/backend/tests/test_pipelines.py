"""Pipeline config read/edit + distribution (FRD-300/303)."""

import pytest
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"

_STEPS = [
    {"type": "injection_filter", "config": {"mode": "llm", "action": "block"}},
    {
        "type": "model_route",
        "config": {
            "model": "router",
            "categories": [{"name": "code", "description": "coding", "model": "strong-1"}],
        },
    },
]


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


def test_get_returns_empty_default() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).get(f"{BASE}demo-uc/pipeline/")
    assert resp.status_code == 200
    assert resp.json() == {"steps": [], "fallback_models": []}


def test_put_saves_and_emits(captured_events) -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": _STEPS, "fallback_models": ["backup-1"]},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["fallback_models"] == ["backup-1"]

    config = PipelineConfig.objects.get(use_case__slug="demo-uc")
    assert config.steps == _STEPS
    published = [p for t, p in captured_events if t == "pipeline.upserted"]
    assert published == [{"use_case": "demo-uc", "steps": _STEPS, "fallback_models": ["backup-1"]}]


def test_put_is_idempotent_update() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    client = _client(admin)
    client.put(f"{BASE}demo-uc/pipeline/", {"steps": _STEPS, "fallback_models": []}, format="json")
    client.put(f"{BASE}demo-uc/pipeline/", {"steps": [], "fallback_models": ["x"]}, format="json")
    assert PipelineConfig.objects.filter(use_case__slug="demo-uc").count() == 1
    config = PipelineConfig.objects.get(use_case__slug="demo-uc")
    assert config.steps == []
    assert config.fallback_models == ["x"]


def test_put_rejects_invalid_step_type() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [{"type": "nope"}], "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_non_list_steps() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": "notalist", "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_non_dict_step_config() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [{"type": "allow_check", "config": "oops"}], "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_bad_fallback_models() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [], "fallback_models": [1, 2]},
        format="json",
    )
    assert resp.status_code == 400


def test_put_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).put(
        f"{BASE}demo-uc/pipeline/", {"steps": [], "fallback_models": []}, format="json"
    )
    assert resp.status_code == 403


def test_member_may_read_pipeline() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob", "use-case-user")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    assert _client(member).get(f"{BASE}demo-uc/pipeline/").status_code == 200


def test_str_representation() -> None:
    admin = _user("admin1", "use-case-admin")
    uc = _make_uc(admin, "demo-uc")
    config = PipelineConfig.objects.create(use_case=uc)
    assert str(config) == "pipeline for demo-uc"
