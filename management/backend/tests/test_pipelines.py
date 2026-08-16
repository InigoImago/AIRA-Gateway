"""Pipeline config read/edit + distribution (FRD-300/303)."""

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"

#: What `_STEPS` names, and what a use case therefore has to have been released before it can be
#: saved (`FRD-308`).
_STEP_MODELS = ("router", "strong-1", "b", "backup-1")

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
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_uc(admin, slug: str = "demo-uc", releases: tuple[str, ...] = ()) -> UseCase:
    """A use case, plus the models a pipeline is allowed to name (`FRD-308`).

    Releasing is a **required step** now, not fixture noise: a pipeline may only name models the
    use case may call, so a test that saves a routing rule has to have released its target. Stated
    per test rather than hidden in the helper's default, because "release first" is the order an
    administrator has to learn too.
    """
    _client(admin).post(BASE, {"slug": slug, "name": "Demo"}, format="json")
    usecase = UseCase.objects.get(slug=slug)
    if releases:
        usecase.allowed_models.set(
            [Model.objects.create(name=name, approved=True) for name in releases]
        )
    return usecase


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def test_get_returns_empty_default() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).get(f"{BASE}demo-uc/pipeline/")
    assert resp.status_code == 200
    assert resp.json() == {"steps": [], "fallback_models": [], "start_model": ""}


def test_get_returns_saved_config() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc", _STEP_MODELS)
    client = _client(admin)
    client.put(
        f"{BASE}demo-uc/pipeline/", {"steps": _STEPS, "fallback_models": ["b"]}, format="json"
    )
    resp = client.get(f"{BASE}demo-uc/pipeline/")
    assert resp.status_code == 200
    assert resp.json()["steps"] == _STEPS
    assert resp.json()["fallback_models"] == ["b"]


def test_put_saves_and_emits(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc", _STEP_MODELS)

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
    assert published == [
        {
            "use_case": "demo-uc",
            "steps": _STEPS,
            "fallback_models": ["backup-1"],
            # Where a caller who names no model enters (`ADR-0020`). Blank here, which is a real
            # state: this pipeline is only ever reached by a request that names its own model.
            "start_model": "",
        }
    ]


def test_put_is_idempotent_update() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc", (*_STEP_MODELS, "x"))
    client = _client(admin)
    client.put(f"{BASE}demo-uc/pipeline/", {"steps": _STEPS, "fallback_models": []}, format="json")
    client.put(f"{BASE}demo-uc/pipeline/", {"steps": [], "fallback_models": ["x"]}, format="json")
    assert PipelineConfig.objects.filter(use_case__slug="demo-uc").count() == 1
    config = PipelineConfig.objects.get(use_case__slug="demo-uc")
    assert config.steps == []
    assert config.fallback_models == ["x"]


def test_put_rejects_invalid_step_type() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [{"type": "nope"}], "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_non_list_steps() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": "notalist", "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_non_dict_step_config() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [{"type": "injection_filter", "config": "oops"}], "fallback_models": []},
        format="json",
    )
    assert resp.status_code == 400


def test_put_rejects_bad_fallback_models() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {"steps": [], "fallback_models": [1, 2]},
        format="json",
    )
    assert resp.status_code == 400


def test_put_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).put(
        f"{BASE}demo-uc/pipeline/", {"steps": [], "fallback_models": []}, format="json"
    )
    assert resp.status_code == 403


def test_member_may_read_pipeline() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    assert _client(member).get(f"{BASE}demo-uc/pipeline/").status_code == 200


def test_str_representation() -> None:
    admin = _user("admin1", "global-admin")
    uc = _make_uc(admin, "demo-uc")
    config = PipelineConfig.objects.create(use_case=uc)
    assert str(config) == "pipeline for demo-uc"


# ---- config bounds (ADR-0007) --------------------------------------------------------------


def _save(steps=None, fallback_models=None, releases=("strong-1", "cheap-1", "router-1", "m")):
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc", releases)
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
    resp = _save([{"type": "injection_filter", "config": {}}] * 40)
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


def test_rejects_a_step_type_that_is_not_in_the_vocabulary() -> None:
    """`allow_check` is one of those now (`FRD-308`). The bound it used to carry — how many models
    a use case may name — moved with the rule, to `UseCaseSerializer.validate_allowed_models`."""
    resp = _save([{"type": "allow_check", "config": {"models": ["m"]}}])
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


def test_the_undetermined_policy_is_validated_where_it_is_authored() -> None:
    """`FRD-125` gave a blocking LLM filter a choice about what to do when its classifier reaches
    no verdict. The gateway reads anything that is not "allow" as blocking, so a typo is *safe* —
    and silently means the opposite of what somebody typed. Caught here, where they typed it."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).put(
        f"{BASE}demo-uc/pipeline/",
        {
            "steps": [
                {
                    "type": "injection_filter",
                    "config": {"mode": "llm", "action": "block", "on_undetermined": "blok"},
                }
            ],
            "fallback_models": [],
        },
        format="json",
    )

    assert resp.status_code == 400
    assert "on_undetermined" in str(resp.json())


def test_both_undetermined_policies_are_accepted() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    client = _client(admin)

    for policy in ("block", "allow"):
        resp = client.put(
            f"{BASE}demo-uc/pipeline/",
            {
                "steps": [
                    {
                        "type": "injection_filter",
                        "config": {"mode": "llm", "on_undetermined": policy},
                    }
                ],
                "fallback_models": [],
            },
            format="json",
        )
        assert resp.status_code == 200, policy


# ---- a pipeline may only name models the use case may call (`FRD-308`) -------------------


def test_a_routing_target_the_use_case_may_not_call_is_refused_by_name() -> None:
    """The gateway refuses it at dispatch anyway, so this is not the only check and is not meant
    to be — it is the one that arrives while somebody can still fix it. Without it a builder
    happily saves a rule pointing at a model the use case may not call, and the failure surfaces
    later as refused traffic on a configuration that looks correct."""
    resp = _save(
        [
            {
                "type": "model_route",
                "config": {
                    "model": "router-1",
                    "categories": [{"name": "code", "model": "not-released-1"}],
                },
            }
        ],
        releases=("router-1",),
    )

    assert resp.status_code == 400
    assert "not-released-1" in str(resp.data)
    # And the one that *was* released is not blamed for it.
    assert "router-1" not in str(resp.data)


def test_every_place_a_model_can_be_written_is_checked() -> None:
    """Five of them: the filter's classifier, the router's classifier, a category target, the
    default target and the fallback chain. A check that read one would refuse the obvious mistake
    and leave four."""
    cases = [
        ([{"type": "injection_filter", "config": {"mode": "llm", "model": "sneak-1"}}], []),
        ([{"type": "model_route", "config": {"model": "sneak-1"}}], []),
        (
            [
                {
                    "type": "model_route",
                    "config": {"categories": [{"name": "c", "model": "sneak-1"}]},
                }
            ],
            [],
        ),
        ([{"type": "model_route", "config": {"default_model": "sneak-1"}}], []),
        ([], ["sneak-1"]),
    ]
    admin = _user("many", "global-admin")
    usecase = _make_uc(admin, "demo-uc", ("allowed-1",))
    for steps, fallbacks in cases:
        resp = _client(admin).put(
            f"{BASE}{usecase.slug}/pipeline/",
            {"steps": steps, "fallback_models": fallbacks},
            format="json",
        )
        assert resp.status_code == 400, (steps, fallbacks)
        assert "sneak-1" in str(resp.data), (steps, fallbacks)


def test_a_use_case_with_nothing_released_may_still_save_a_pipeline_that_names_nothing() -> None:
    """The honest order: such a use case can serve nothing either, so refusing an empty pipeline
    would be refusing something harmless while the real refusal already happens at dispatch."""
    resp = _save([{"type": "injection_filter", "config": {"mode": "heuristic"}}], releases=())

    assert resp.status_code == 200


def test_a_released_model_saves() -> None:
    resp = _save(
        [{"type": "model_route", "config": {"categories": [{"name": "c", "model": "cheap-1"}]}}],
        ["cheap-1"],
        releases=("cheap-1",),
    )

    assert resp.status_code == 200
