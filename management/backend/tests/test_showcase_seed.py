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
    from aira_management.apps.seed.contributions import local_models

    _seed_local_models_against_a_serving_endpoint(local_models)

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
    """Cost, tokens and requests; both scopes; day and month. A control with no example on screen
    is a control somebody has to be told about.

    Two scopes rather than three since 2026-08-14 — the one naming an individual was removed — and
    `each_member` is the one an administrator wanted anyway: a fair share per head, no names to
    keep up to date, and it keeps applying to whoever joins."""
    budgets = list(Budget.objects.all())
    assert any(b.limit_cost for b in budgets)
    assert any(b.limit_tokens for b in budgets)
    assert any(b.limit_requests for b in budgets)
    assert {b.scope for b in budgets} == {Budget.USE_CASE, Budget.EACH_MEMBER}
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


# `test_the_handover_offers_only_a_model_the_gateway_will_serve` stood here until 2026-08-13, and
# it is **removed rather than repaired**, because what it tested moved out of the seed.
#
# It called `showcase_agent.config("qwen3:0.6b", key)` and asserted the menu held that one model —
# a fair test while the config *named* a model from an environment variable. It does not any more:
# the menu is derived at run time from what the gateway serves and what this use case may call, so
# the seed no longer decides it and a seed test can no longer assert it. The property lives in
# `tools/tests/test_showcase_agent_config.py`, where it is checked in both directions.
#
# The seed's own half of the claim is still here, two tests up: the assistant has a model whose
# catalog entry declares `tools`, which is the precondition for anything reaching a menu at all.
#
# Worth recording how it was found. The signature changed two commits before this broke, and the
# runs after it were **subsets** — `tools/tests`, ruff, mypy, the frontend — so a stale call sat in
# a green-looking tree until the next full suite. A subset that passes is not a suite that passes.


def test_a_seeded_model_is_announced_and_not_only_written() -> None:
    """**The catalog has to reach the gateway, or nothing can be served at all.**

    Found on a machine that had never run this stack before: `make showcase` drove ten requests,
    all ten came back `400 … 'qwen3:0.6b' is not in the model catalog`, and the target reported
    success. Management's catalog had both models; the gateway's read-model had none, because this
    contribution wrote the rows and emitted nothing. Only the viewset emitted, so a model declared
    through the console worked and a model declared by the seed did not.

    `FRD-307` is what turned that from invisible into total: since only a catalogued, approved
    model may be used, an unannounced catalog refuses every request. The fourth instance in this
    repository of two correct halves with no wire between them.
    """
    from aira_management.apps.seed.contributions import local_models
    from aira_management.apps.usecases import events

    recorded: list[tuple[str, dict]] = []
    subscriber = events.subscribe(
        lambda event_type, payload: recorded.append((event_type, payload))
    )
    try:
        _seed_local_models_against_a_serving_endpoint(local_models)
    finally:
        events.unsubscribe(subscriber)

    announced = {
        payload["name"] for event_type, payload in recorded if event_type == "model.upserted"
    }

    assert announced, "the seed declared models and told the gateway about none of them"
    # The payload is the viewset's, so a field added there travels from here too. Asserted on a
    # field that only exists because somebody remembered it: a price that arrives as a float is a
    # cost nobody can reconcile.
    upserts = [payload for event_type, payload in recorded if event_type == "model.upserted"]
    assert all("input_price_per_million" in payload for payload in upserts)
    assert all(
        payload["input_price_per_million"] is None
        or isinstance(payload["input_price_per_million"], str)
        for payload in upserts
    )


def _seed_local_models_against_a_serving_endpoint(module) -> None:  # noqa: ANN001
    """Run the local-model seed as if the endpoint were up and serving everything it declares.

    Reachability is a *separate* property with its own test below. Stubbing it here keeps these
    two about what they are named after — that a declaration is announced, and that a tools-capable
    model exists for the assistant use case.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIRA_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(
        module, "_served_models", lambda: {d["name"] for d in module._declarations()}
    )
    try:
        module.seed_local_models(fresh=False)
    finally:
        monkeypatch.undo()


def test_a_model_the_endpoint_does_not_serve_is_never_catalogued() -> None:
    """`FRD-130`'s rule, enforced by evidence instead of by ordering.

    The seed container used to wait for the model **pull** to succeed, so one failed download meant
    the seed never ran at all — no accounts, no use cases, no budgets, and a console that came up
    empty with nothing connecting the two. It runs regardless now, and the models are declared from
    what the endpoint answers: a model nobody pulled is still never catalogued, because every
    request against it would fail with `model_not_found`.
    """
    from aira_management.apps.catalog.models import Model
    from aira_management.apps.seed.contributions import local_models

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIRA_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(local_models, "_served_models", lambda: {"something-else"})
    try:
        local_models.seed_local_models(fresh=False)
    finally:
        monkeypatch.undo()

    assert not Model.objects.filter(platform="ollama").exists()


def test_an_endpoint_that_cannot_be_asked_declares_nothing() -> None:
    """Unreachable is not "serves nothing" and it is certainly not "serves everything". Absence of
    information is not permission — the same rule as `FRD-114` FR-7, one layer out."""
    from aira_management.apps.catalog.models import Model
    from aira_management.apps.seed.contributions import local_models

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIRA_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(local_models, "_served_models", lambda: None)
    try:
        local_models.seed_local_models(fresh=False)
    finally:
        monkeypatch.undo()

    assert not Model.objects.filter(platform="ollama").exists()


def test_a_model_whose_tag_is_implicit_is_recognised() -> None:
    """An absent tag means `:latest`, and the endpoint answers with the explicit form.

    The catalog says `all-minilm`, the listing says `all-minilm:latest`. Comparing them as plain
    strings quietly dropped the embedding model — a regression introduced by the reachability check
    itself and caught by running it, not by reading it. Same family as the colon that once split
    `qwen3:0.6b` into a model nobody served.
    """
    from aira_management.apps.catalog.models import Model
    from aira_management.apps.seed.contributions import local_models

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIRA_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(
        local_models,
        "_served_models",
        lambda: {
            f"{d['name']}:latest" if ":" not in str(d["name"]) else d["name"]
            for d in local_models._declarations()
        },
    )
    try:
        local_models.seed_local_models(fresh=False)
    finally:
        monkeypatch.undo()

    declared = set(Model.objects.filter(platform="ollama").values_list("name", flat=True))
    assert {d["name"] for d in local_models._declarations()} <= declared


def test_every_demo_use_case_can_actually_call_a_model(seeded) -> None:  # noqa: ARG001
    """`FRD-308`: a use case with nothing released refuses **every** request.

    That rule is the whole feature, and it is also the fastest way to ship a demo that answers ten
    refusals and looks broken — the shape `FRD-500` records, where a control that blocks wrongly
    on its first run gets switched off for good. So the seed releases explicitly, which is what a
    real installation does too; what it must never do is *default* to it.
    """
    from aira_management.apps.catalog.models import Model
    from aira_management.apps.usecases.models import UseCase

    # The catalog is seeded first in a real run (`local_models` is `order=40`, the showcase is
    # `order=50`) and needs a live endpoint to know what to declare. Standing one in here states
    # the dependency instead of assuming it — a showcase that released nothing because the catalog
    # was empty is precisely the failure this guards.
    Model.objects.create(name="demo-chat", approved=True)
    Model.objects.create(name="not-released-yet", approved=False)
    seed_showcase(fresh=False)

    approved = {m.name for m in Model.objects.filter(approved=True)}
    assert approved == {"demo-chat"}

    for usecase in UseCase.objects.all():
        released = {m.name for m in usecase.allowed_models.all()}
        # `entwicklung` is deliberately narrower — the demo's own example of the feature — and it
        # names a model the catalog here does not have, so it lands on the empty intersection.
        # Every other use case gets what the installation approved.
        expected = set() if usecase.slug == "entwicklung" else approved
        assert released == expected, usecase.slug


def test_what_it_releases_travels_to_the_gateway(seeded) -> None:
    """A release the gateway never hears about is a use case that refuses everything while the
    console shows it fully configured — two correct halves and no wire, the failure this project
    has now recorded five times."""
    from aira_management.apps.usecases.models import UseCase

    _result, recorded = seeded
    upserted = [p for t, p in recorded if t == "usecase.upserted"]
    assert upserted, "no use case was announced at all"
    for payload in upserted:
        expected = sorted(
            m.name for m in UseCase.objects.get(slug=payload["slug"]).allowed_models.all()
        )
        assert payload["allowed_models"] == expected, payload["slug"]


# == what the endpoint answers while it is still pulling (found from an empty machine) ============


def test_a_listing_with_a_null_data_field_is_no_models_rather_than_a_crash(monkeypatch) -> None:
    """Ollama answers `{"data": null}` while it serves nothing — which is exactly the state on a
    first run, mid-pull.

    `payload.get("data", [])` defaults only when the key is **absent**, so this raised
    `TypeError: 'NoneType' object is not iterable` and took the whole seed down: no demo accounts,
    no use cases, no API keys — and `make showcase` drove traffic anyway and met eleven 401s.
    Measured on 2026-08-12 from a machine with no model downloaded, which is the machine this
    target exists for.

    The mechanism it broke is the one written to answer *"which models are really there"* with
    evidence instead of with ordering (`docker-compose.apps.yml`), and it crashed in the single
    case it exists for. *Absent and empty are different answers* — here, between absent and null.
    """
    import io
    import json
    from contextlib import contextmanager

    from aira_management.apps.seed.contributions import local_models

    @contextmanager
    def _answering(payload: dict[str, object]):
        yield io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(local_models, "_local_endpoint", lambda: "http://ollama:11434")
    monkeypatch.setattr(
        local_models.urllib.request, "urlopen", lambda *a, **k: _answering({"data": None})
    )

    # An answer, and the answer is "none" — not an exception, and not `None`, which this function
    # reserves for "the endpoint could not be reached at all".
    assert local_models._served_models() == set()


def test_a_listing_that_names_models_is_still_read(monkeypatch) -> None:
    """The guard against fixing the crash by returning nothing in every case."""
    import io
    import json
    from contextlib import contextmanager

    from aira_management.apps.seed.contributions import local_models

    @contextmanager
    def _answering(payload: dict[str, object]):
        yield io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(local_models, "_local_endpoint", lambda: "http://ollama:11434")
    monkeypatch.setattr(
        local_models.urllib.request,
        "urlopen",
        lambda *a, **k: _answering({"data": [{"id": "qwen3:0.6b"}, {"id": "all-minilm"}]}),
    )

    assert local_models._served_models() == {"qwen3:0.6b", "all-minilm:latest"}
