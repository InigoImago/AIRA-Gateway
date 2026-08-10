"""The demo seed produces a walkthrough, not just rows (FRD-130).

What this checks is not that the objects exist — a seed that creates three use cases and grants
nobody anything would pass that. It checks the two properties a walkthrough actually depends on:

- **the roles see different things**, so switching accounts demonstrates the scoping instead of
  illustrating it;
- **every object is announced**, because a seed that writes Management directly leaves the
  gateway's read model empty, and the resulting demo shows use cases in the UI that refuse every
  request made against them.
"""

import pytest
from aira_management.apps.budgets.models import Budget
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.seed.contributions.roles_and_users import seed_roles_and_users
from aira_management.apps.seed.contributions.showcase import _use_cases, seed_showcase
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import scope_queryset, sync_user_roles
from django.contrib.auth import get_user_model

from .conftest import role_claims

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    seed_roles_and_users(fresh=False)
    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event_type, payload: recorded.append((event_type, payload)))
    result = seed_showcase(fresh=False)
    return result, recorded


def test_it_creates_the_three_use_cases(seeded) -> None:
    assert set(UseCase.objects.values_list("slug", flat=True)) >= {
        "kundenservice",
        "entwicklung",
        "personalwesen",
        "coding-assistant",
    }


def test_the_assistant_is_the_only_use_case_allowed_to_declare_functions(seeded) -> None:
    """`FRD-131` FR-3: off by default is least privilege, and a demo in which everything has it
    teaches the opposite. It is also what makes `make showcase` end at a working assistant instead
    of at a refusal — the README has pointed at a `coding-assistant` use case since `FRD-132` and
    nothing created one.
    """
    enabled = set(UseCase.objects.filter(tools_enabled=True).values_list("slug", flat=True))

    assert enabled == {"coding-assistant"}


def test_the_assistant_has_a_model_that_can_actually_call_a_function(seeded) -> None:
    """The other half, and the half that fails silently. A use case may declare functions and the
    dispatch chain still refuses **by name** unless the model's catalog entry declares `tools` —
    which the Management-side seed did not, while the gateway-side one did. Two seeds, one
    measurement, one of them wrong.

    The endpoint is *configured for the test* rather than skipped over. A skip here would report
    green about nothing — the catalog is empty exactly when this contribution does not run, so the
    condition that hides the defect is the condition that hides the test.
    """
    from aira_management.apps.catalog.models import Model
    from aira_management.apps.seed.contributions.local_models import seed_local_models

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIRA_OLLAMA_URL", "http://ollama:11434")
    try:
        seed_local_models(fresh=False)
    finally:
        monkeypatch.undo()

    # In Python, not in the query: `__contains` on a JSON field is Postgres-only and the
    # hermetic suite runs on SQLite — the same split `FRD-505` hit when it derived `flagged` in
    # code rather than querying the decisions.
    declared = [set(model.capabilities or []) for model in Model.objects.all()]

    assert any("tools" in capabilities for capabilities in declared), (
        "the assistant use case may declare functions and no declared model may receive them"
    )


def test_running_it_twice_changes_nothing(seeded) -> None:
    """A demo that has to be reset before it can be shown is a demo nobody shows."""
    before = UseCase.objects.count(), Budget.objects.count(), RateLimit.objects.count()
    seed_showcase(fresh=False)
    assert (UseCase.objects.count(), Budget.objects.count(), RateLimit.objects.count()) == before


def test_the_roles_do_not_all_see_the_same_thing(seeded) -> None:
    """**The property the walkthrough rests on.** `ucadmin` administers two of three; governance
    sees all three. If those were equal, switching accounts would demonstrate nothing."""
    users = {u.get_username(): u for u in get_user_model().objects.all()}
    # `ucadmin` holds **no** organisation-wide role (`ADR-0017`): what it administers comes from
    # the grants the seed writes on two of the three use cases, which is exactly the property this
    # test is about. Giving it a role here would have made the seed's grants irrelevant to the
    # answer and the assertion would have passed on the wrong evidence.
    sync_user_roles(users["ucadmin"], role_claims())
    sync_user_roles(users["itgov"], role_claims("it-steuerung"))

    admin_sees = set(
        scope_queryset(
            users["ucadmin"], "usecases.view_usecase", UseCase.objects.all()
        ).values_list("slug", flat=True)
    )
    governance_sees = set(
        scope_queryset(users["itgov"], "usecases.view_usecase", UseCase.objects.all()).values_list(
            "slug", flat=True
        )
    )

    assert "personalwesen" not in admin_sees, "the scoping is invisible in the demo"
    assert {"kundenservice", "entwicklung"} <= admin_sees
    assert {"kundenservice", "entwicklung", "personalwesen"} <= governance_sees


def test_every_budget_axis_has_a_live_example(seeded) -> None:
    """Cost, tokens and requests; use-case and member scope; day and month. A control with no
    example on screen is a control somebody has to be told about."""
    budgets = list(Budget.objects.all())
    assert any(b.limit_cost for b in budgets)
    assert any(b.limit_tokens for b in budgets)
    assert any(b.limit_requests for b in budgets)
    assert {b.scope for b in budgets} == {Budget.USE_CASE, Budget.MEMBER}
    assert {b.period for b in budgets} == {Budget.DAY, Budget.MONTH}


def test_everything_it_creates_is_announced(seeded) -> None:
    """A seed that wrote the tables directly would populate Management and leave the gateway's read
    model empty — use cases visible in the UI, every request against them refused. That is the most
    confusing state to hand somebody, so the events are part of the contract."""
    _, recorded = seeded
    kinds = {event_type for event_type, _ in recorded}

    assert kinds >= {
        "usecase.upserted",
        "membership.upserted",
        "budget.upserted",
        "ratelimit.upserted",
        "pipeline.upserted",
        "api_key.created",
    }


def test_the_api_keys_it_reports_are_the_ones_it_stored(seeded) -> None:
    """The plaintext it prints has to authenticate, or the demo's first gateway call fails and
    everything after it is guesswork."""
    from aira_management.apps.apikeys.models import ApiKey

    from aira_common.apikeys import hash_api_key, parse_prefix

    result, _ = seeded
    for slug, plaintext in result["api_keys_plaintext"].items():
        stored = ApiKey.objects.get(prefix=parse_prefix(plaintext))
        assert stored.use_case.slug == slug
        assert stored.key_hash == hash_api_key(plaintext)


def test_the_pipeline_does_not_seed_the_classifier_that_blocks_everything(seeded) -> None:
    """`FRD-125` §9: against a 0.6B model the LLM filter answers INJECTION to everything. Seeding it
    would demonstrate a filter blocking innocent questions and teach the wrong lesson — the builder
    offers it, the demo does not choose it."""
    from aira_management.apps.pipelines.models import PipelineConfig

    for config in PipelineConfig.objects.all():
        for step in config.steps:
            assert step.get("config", {}).get("mode") != "llm"


def test_a_fresh_run_clears_the_leftovers_of_every_test_that_came_before(seeded) -> None:
    """A demo database accumulates fixtures. On the first walkthrough here it held **801** use
    cases with names like `burst-3i6g5l`, and a global administrator opening that list learns only
    that the list is long.

    `--fresh` therefore means *every* use case, not only the ones this contribution made — and the
    deletions are announced, or the gateway keeps serving read-model rows for use cases Management
    no longer has.
    """
    UseCase.objects.create(slug="burst-3i6g5l", name="left over by a test")

    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event_type, payload: recorded.append((event_type, payload)))
    seed_showcase(fresh=True)

    assert not UseCase.objects.filter(slug="burst-3i6g5l").exists()
    assert ("usecase.deleted", {"slug": "burst-3i6g5l"}) in recorded
    # And the demo's own are back, all of them.
    assert UseCase.objects.count() == len(_use_cases())


def test_a_fresh_run_does_not_revoke_the_keys_it_is_about_to_reissue(seeded) -> None:
    """Found by resetting the demo and watching every key answer 401 for ever.

    Deleting a use case revokes its API keys, and revocation is **terminal** in the gateway's
    read model — `api_key.created` must never resurrect one, which is the right rule. Announcing
    a delete for a slug this run is about to recreate therefore kills its keys permanently, and
    the deterministic demo key can never be reissued. Recreating the same slug is a reset, not a
    retirement.
    """
    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event_type, payload: recorded.append((event_type, payload)))
    seed_showcase(fresh=True)

    deleted = {payload["slug"] for kind, payload in recorded if kind == "usecase.deleted"}
    created = {payload["slug"] for kind, payload in recorded if kind == "usecase.upserted"}

    assert not (deleted & created), f"reset announced as retirement for {deleted & created}"


@pytest.mark.django_db
def test_a_membership_the_declaration_no_longer_names_is_removed() -> None:
    """Found by asking the running stack who could manage what.

    `itgov` was still administering `personalwesen` and `itsec` still belonged to
    `kundenservice`, both from declarations that no longer exist. A seed that only ever adds
    cannot be re-run to a known state, which is most of what a seed is for — and a stale
    membership is not a stale row, it is live permission on a use case.
    """
    from aira_management.apps.usecases.access import may_manage
    from aira_management.apps.usecases.models import UseCaseMembership

    seed_roles_and_users(fresh=False)
    seed_showcase(fresh=False)
    usecase = UseCase.objects.get(slug="kundenservice")
    intruder = get_user_model().objects.create(username="left-the-team")
    UseCaseMembership.objects.create(use_case=usecase, user=intruder, role=UseCaseMembership.ADMIN)
    _grant(intruder, usecase, UseCaseMembership.ADMIN)
    assert may_manage(get_user_model().objects.get(pk=intruder.pk), usecase)

    seed_showcase(fresh=False)

    assert not UseCaseMembership.objects.filter(use_case=usecase, user=intruder).exists()
    # And the permission it granted is gone with it, not left behind as an invisible grant.
    assert not may_manage(get_user_model().objects.get(pk=intruder.pk), usecase)


@pytest.mark.django_db
def test_an_oversight_role_administers_nothing_in_the_demo() -> None:
    """PRD §154 gives IT Steuerung every figure and no write anywhere. A walkthrough in which it
    renames a use case demonstrates a boundary that does not exist."""
    from aira_management.apps.usecases.access import may_manage

    seed_roles_and_users(fresh=False)
    seed_showcase(fresh=False)
    users = get_user_model().objects

    for username in ("itgov", "itsec"):
        user = users.filter(username=username).first()
        if user is None:
            continue
        for usecase in UseCase.objects.all():
            assert not may_manage(user, usecase), f"{username} administers {usecase.slug}"


def test_the_handover_derives_the_key_the_seed_actually_stored(seeded) -> None:
    """`tools/showcase_agent.py` re-derives the demo key rather than reading it, because it runs
    outside Django — so it carries its own copy of the salt, and a copy is a thing that drifts.

    The failure would be quiet in the worst way: a config file that looks right, an assistant that
    starts, and a 401 the reader blames on the gateway. Compared against the stored **hash**, so
    this asserts the key works rather than that two constants match.
    """
    import importlib.util
    import pathlib

    from aira_management.apps.apikeys.models import ApiKey

    from aira_common.apikeys import hash_api_key

    path = pathlib.Path(__file__).resolve().parents[3] / "tools" / "showcase_agent.py"
    spec = importlib.util.spec_from_file_location("showcase_agent", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    key = ApiKey.objects.get(use_case__slug="coding-assistant")

    assert hash_api_key(module.demo_key("coding-assistant")) == key.key_hash


def test_the_handover_offers_only_a_model_the_gateway_will_serve(seeded) -> None:
    """OpenCode lists whatever the config declares. A menu naming a model the gateway refuses is
    `FRD-206`'s complaint in another client — an action offered that cannot be carried out."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[3] / "tools" / "showcase_agent.py"
    spec = importlib.util.spec_from_file_location("showcase_agent", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    config = module.config("qwen3:0.6b", "aira_x_y")

    assert list(config["provider"]["aira"]["models"]) == ["qwen3:0.6b"]
    assert config["model"] == "aira/qwen3:0.6b"
