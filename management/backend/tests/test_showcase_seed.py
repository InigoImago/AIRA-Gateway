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
from aira_management.apps.seed.contributions.showcase import seed_showcase
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import scope_queryset, sync_user_roles
from django.contrib.auth import get_user_model

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
    }


def test_running_it_twice_changes_nothing(seeded) -> None:
    """A demo that has to be reset before it can be shown is a demo nobody shows."""
    before = UseCase.objects.count(), Budget.objects.count(), RateLimit.objects.count()
    seed_showcase(fresh=False)
    assert (UseCase.objects.count(), Budget.objects.count(), RateLimit.objects.count()) == before


def test_the_roles_do_not_all_see_the_same_thing(seeded) -> None:
    """**The property the walkthrough rests on.** `ucadmin` administers two of three; governance
    sees all three. If those were equal, switching accounts would demonstrate nothing."""
    users = {u.get_username(): u for u in get_user_model().objects.all()}
    sync_user_roles(users["ucadmin"], {"realm_access": {"roles": ["use-case-admin"]}})
    sync_user_roles(users["itgov"], {"realm_access": {"roles": ["it-steuerung"]}})

    admin_sees = set(
        scope_queryset(users["ucadmin"], "usecases.view_usecase", UseCase.objects.all())
        .values_list("slug", flat=True)
    )
    governance_sees = set(
        scope_queryset(users["itgov"], "usecases.view_usecase", UseCase.objects.all())
        .values_list("slug", flat=True)
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
    # And the demo's own three are back.
    assert UseCase.objects.count() == 3


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
