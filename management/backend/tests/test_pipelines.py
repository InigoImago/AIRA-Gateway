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
    # No row is a pipeline with no steps that follows the installation's fallback chains — empty
    # until somebody sets one (`FRD-310`).
    assert resp.json() == {
        "steps": [],
        "fallback_models": [],
        "fallback_mode": "default",
        "fallbacks": {"generate": [], "embed": [], "rerank": []},
    }


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
            # A chain of its own and nothing said about it: what such a pipeline always meant.
            "fallback_mode": "own",
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


@pytest.mark.parametrize(
    ("pattern", "shape"),
    [
        ("(a+)+$", "group whose contents already repeat"),
        # Measured at 1 532 ms on 32 characters and over five seconds on a `\\s*` chain, while
        # `is_catastrophic` answered False (2026-09-08). A prompt is far longer than 32 characters.
        ("a*a*a*a*a*a*a*a*b", "same characters twice in a row"),
        # Cubic in the prompt — 2.8 s on 2 000 characters — and accepted by both rules above,
        # because a back-reference was read as consuming nothing.
        (r"(.*)\1!", "refers back to what a group matched"),
    ],
)
def test_rejects_catastrophic_backtracking_pattern(pattern: str, shape: str) -> None:
    """Either shape could stall a gateway worker on every request of the use case.

    The **message** is asserted as well as the status, because a refusal that names the wrong shape
    sends an operator looking for a repeated group that is not there — which is what this endpoint
    said for as long as it had one sentence for two rules.
    """
    resp = _save([{"type": "injection_filter", "config": {"patterns": [pattern]}}])
    assert resp.status_code == 400
    assert shape in str(resp.json()), resp.json()


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
    """Every name released, so the release check has nothing to say and the bound has to."""
    chain = [f"m{i}" for i in range(20)]

    response = _save([], chain, releases=tuple(chain))

    assert response.status_code == 400
    assert "At most 16 fallback models are allowed." in str(response.json())


def test_rejects_overlong_fallback_model_name() -> None:
    response = _save([], ["x" * 200], releases=("x" * 200,))

    assert response.status_code == 400
    assert "Model names are limited to 128 characters." in str(response.json())


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
    """**Six** of them: the filter's classifier, the router's classifier, a category target, the
    default target, the use case's fallback chain and — since `FRD-302` FR-7 — each step's own
    chain. A check that read one would refuse the obvious mistake and leave five.

    The last one is why this check is load-bearing rather than courteous: a step's model call
    applies no dispatch condition *because* an administrator wrote the name and this saw it
    (`test_every_dispatch_applies_the_conditions.py`). A chain nobody validated would be the hole
    `allow_check`'s removal closed, one field along.
    """
    cases = [
        ([{"type": "injection_filter", "config": {"mode": "llm", "model": "sneak-1"}}], []),
        ([{"type": "model_route", "config": {"model": "sneak-1"}}], []),
        (
            [
                {
                    "type": "injection_filter",
                    "config": {"mode": "llm", "model": "allowed-1", "fallback_models": ["sneak-1"]},
                }
            ],
            [],
        ),
        (
            [
                {
                    "type": "pii_filter",
                    "config": {"model": "allowed-1", "fallback_models": ["sneak-1"]},
                }
            ],
            [],
        ),
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


# ---- a step's own chain (`FRD-302` FR-7) ---------------------------------------------------


def test_a_step_chain_of_released_models_saves() -> None:
    """The ordinary case: a redactor with a second model the use case also trusts."""
    resp = _save(
        [
            {
                "type": "pii_filter",
                "config": {"model": "strong-1", "fallback_models": ["cheap-1"]},
            }
        ],
        releases=("strong-1", "cheap-1"),
    )

    assert resp.status_code == 200, resp.data


@pytest.mark.parametrize(
    ("chain", "said"),
    [
        ([f"m{index}" for index in range(20)], "allows at most 16 items"),
        (["x" * 200], "strings of at most 128 characters"),
        ("cheap-1", "fallback_models must be a list"),
        ([{"model": "cheap-1"}], "strings of at most 128 characters"),
    ],
)
def test_a_step_chain_is_bounded_where_it_is_authored(chain: object, said: str) -> None:
    """Same bounds as the dispatch chain, because it is the same thing one level in — and this
    read-model is executed by the gateway on every request of the use case (`ADR-0007`).

    Every name in the chain is **released**, so the release check passes and only the bound can
    refuse: with the names unreleased, each case was refused by the release check whether or not
    the bound existed.
    """
    names = [name for name in chain if isinstance(name, str)] if isinstance(chain, list) else []
    resp = _save(
        [{"type": "pii_filter", "config": {"model": "m", "fallback_models": chain}}],
        releases=("m", "cheap-1", *names),
    )

    assert resp.status_code == 400
    assert said in str(resp.json()), resp.json()


# ---- the closed vocabularies are refused by name (`M2-3`) -----------------------------------


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"mode": "heuristic", "action": "Block"}, "action"),
        ({"mode": "heuristic", "action": "blok"}, "action"),
        ({"mode": "heuristic", "action": "block", "scope": "system-user"}, "scope"),
        ({"mode": "LLM", "action": "block"}, "mode"),
        ({"mode": "heuristic", "use_builtins": "false"}, "use_builtins"),
        ({"mode": "heuristic", "action": ["block"]}, "action"),
    ],
)
def test_a_filter_word_outside_its_vocabulary_is_refused_by_name(config: dict, field: str) -> None:
    """The gateway blocks only on the exact word `block`, reads an unknown scope as the narrower
    one, and takes the string `"false"` as true — so each of these saved, showed as configured,
    and did the opposite of what was typed. `on_undetermined` was already refused where it is
    written; the words where a typo is *unsafe* are refused the same way."""
    response = _save([{"type": "injection_filter", "config": config}])

    assert response.status_code == 400, response.json()
    assert f"step.config.{field}" in str(response.json()), response.json()


def test_every_word_of_each_vocabulary_is_accepted() -> None:
    """The other half: a validator that refused everything would pass every case above."""
    from aira_common.pipelines import FilterAction, InjectionMode, ScanScope

    admin = _user("vocabulary-admin", "global-admin")
    _make_uc(admin, "vocabulary-uc")
    client = _client(admin)
    for mode in InjectionMode:
        for action in FilterAction:
            for scope in ScanScope:
                for use_builtins in (True, False):
                    config = {
                        "mode": str(mode),
                        "action": str(action),
                        "scope": str(scope),
                        "use_builtins": use_builtins,
                    }
                    response = client.put(
                        f"{BASE}vocabulary-uc/pipeline/",
                        {"steps": [{"type": "injection_filter", "config": config}]},
                        format="json",
                    )
                    assert response.status_code == 200, (config, response.json())


# ---- a value of the wrong kind is refused, never a server error (`M2-5`) ---------------------


@pytest.mark.parametrize("step_type", [["model_route"], {"a": 1}])
def test_a_step_type_that_is_not_a_word_is_refused(step_type: object) -> None:
    """Membership in the step-type `set` hashed the value, and a list or an object raised
    `TypeError` — a `500` for a value the caller typed."""
    response = _save([{"type": step_type}])

    assert response.status_code == 400, response.content
    assert "Each step needs a type" in str(response.json())


# ---- a step carries only what the gateway reads ----------------------------------------------


@pytest.mark.parametrize(
    ("step", "said"),
    [
        ({"type": "pii_filter", "config": {}, "label": "x"}, "does not take 'label'"),
        ({"type": "model_route", "config": {"notes": "x" * 1_500_000}}, "does not take 'notes'"),
        ({"type": "injection_filter", "config": {"notice": "hi"}}, "does not take 'notice'"),
        (
            {"type": "model_route", "config": {"categories": [{"name": "c", "pad": 1}]}},
            "does not take 'pad'",
        ),
    ],
)
def test_a_key_nothing_reads_is_refused_by_name(step: dict, said: str) -> None:
    """A step's configuration travels to the gateway on every save and is executed on every request
    of the use case. A plain administrator saved a 1.5 MB key nothing reads, which became an event
    larger than the bus carries; and a key nothing reads is a setting somebody tunes and then
    wonders why nothing changes. The keys are the ones the gateway's step reads."""
    response = _save([step])

    assert response.status_code == 400, str(response.json())[:300]
    assert said in str(response.json())[:2000]


@pytest.mark.parametrize(
    ("config", "said"),
    [
        ({"model": ["router-1"]}, "step.config.model must be a model name"),
        ({"default_model": "x" * 200}, "step.config.default_model must be a model name"),
        (
            {"categories": [{"name": "c", "model": "x" * 200}]},
            "step.config.categories[0].model must be a model name",
        ),
        ({"categories": [{"name": "x" * 5000}]}, "step.config.categories[0].name must be text"),
    ],
)
def test_a_model_a_step_names_is_a_model_name_and_said_where(config: dict, said: str) -> None:
    """A category's model was reported as `step.config.model … 4000 characters` — the wrong path,
    and a bound that is not a model name's — and a list where a name belongs reached the release
    check as its printed form."""
    response = _save([{"type": "model_route", "config": config}])

    assert response.status_code == 400
    assert said in str(response.json()), response.json()


def test_both_planes_read_one_set_of_bounds() -> None:
    """The bounds Management refuses at are the ones the gateway truncates at, because they are
    the same objects."""
    from aira_management.apps.pipelines import serializers

    from aira_common import patterns, pipelines

    for name in (
        "MAX_STEPS",
        "MAX_FALLBACK_MODELS",
        "MAX_MODEL_LENGTH",
        "MAX_TEXT_LENGTH",
        "MAX_CATEGORIES",
    ):
        assert getattr(serializers, name) is getattr(pipelines, name), name
    assert set(serializers.STEP_TYPES) == set(pipelines.STEP_CONFIG_KEYS)
    for name in ("MAX_PATTERNS", "MAX_PATTERN_LENGTH"):
        assert getattr(serializers, name) is getattr(patterns, name), name
