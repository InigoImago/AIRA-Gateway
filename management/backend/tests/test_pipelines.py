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


def test_get_returns_saved_config() -> None:
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    client = _client(admin)
    client.put(
        f"{BASE}demo-uc/pipeline/", {"steps": _STEPS, "fallback_models": ["b"]}, format="json"
    )
    resp = client.get(f"{BASE}demo-uc/pipeline/")
    assert resp.status_code == 200
    assert resp.json()["steps"] == _STEPS
    assert resp.json()["fallback_models"] == ["b"]


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


# ---- config bounds (ADR-0007) --------------------------------------------------------------


def _save(steps=None, fallback_models=None):
    admin = _user("admin1", "use-case-admin")
    _make_uc(admin, "demo-uc")
    return _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": steps or [], "fallback_models": fallback_models or []},
        format="json",
    )


def test_rejects_catastrophic_backtracking_pattern() -> None:
    """A nested quantifier could stall a gateway worker on every request of the use case."""
    resp = _save([{"type": "injection_filter", "config": {"patterns": ["(a+)+$"]}}])
    assert resp.status_code == 400
    assert "nests quantifiers" in str(resp.json())


def test_accepts_ordinary_custom_pattern() -> None:
    resp = _save([{"type": "injection_filter", "config": {"patterns": ["internal[- ]secret"]}}])
    assert resp.status_code == 200


def test_accepts_invalid_regex_as_literal() -> None:
    resp = _save([{"type": "injection_filter", "config": {"patterns": ["unbalanced("]}}])
    assert resp.status_code == 200


def test_rejects_too_many_steps() -> None:
    resp = _save([{"type": "allow_check", "config": {}}] * 40)
    assert resp.status_code == 400


def test_rejects_overlong_pattern() -> None:
    resp = _save([{"type": "injection_filter", "config": {"patterns": ["x" * 300]}}])
    assert resp.status_code == 400


def test_rejects_too_many_patterns() -> None:
    resp = _save([{"type": "injection_filter", "config": {"patterns": ["a"] * 100}}])
    assert resp.status_code == 400


def test_rejects_non_list_patterns() -> None:
    resp = _save([{"type": "injection_filter", "config": {"patterns": "a"}}])
    assert resp.status_code == 400


def test_rejects_overlong_instruction() -> None:
    resp = _save([{"type": "injection_filter", "config": {"instruction": "x" * 5000}}])
    assert resp.status_code == 400


def test_rejects_too_many_allowed_models() -> None:
    resp = _save([{"type": "allow_check", "config": {"models": ["m"] * 100}}])
    assert resp.status_code == 400


def test_rejects_too_many_categories() -> None:
    categories = [{"name": f"c{i}", "model": "m"} for i in range(40)]
    resp = _save([{"type": "model_route", "config": {"categories": categories}}])
    assert resp.status_code == 400


def test_rejects_non_object_category() -> None:
    resp = _save([{"type": "model_route", "config": {"categories": ["nope"]}}])
    assert resp.status_code == 400


def test_rejects_overlong_category_name() -> None:
    resp = _save([{"type": "model_route", "config": {"categories": [{"name": "x" * 5000}]}}])
    assert resp.status_code == 400


def test_rejects_too_many_fallback_models() -> None:
    assert _save([], [f"m{i}" for i in range(20)]).status_code == 400


def test_rejects_overlong_fallback_model_name() -> None:
    assert _save([], ["x" * 200]).status_code == 400
