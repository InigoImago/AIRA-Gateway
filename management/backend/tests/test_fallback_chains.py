"""A use case's fallback chain for each kind of request, in its pipeline (`FRD-310`, `ADR-0052`).

Three chains — text generation, embedding, ranking — each empty until an administrator of the use
case fills it: a model that cannot answer is then the caller's error, which is the default. A chain
names only models the use case was released and that can serve its kind, so an embedding never
falls back to a model that writes text. The installation keeps no chain of its own any more, and
the migration that removed them carried each one into the use cases that followed it.
"""

from __future__ import annotations

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views.payloads import _pipeline_payload
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

USE_CASES = "/api/v1/use-cases/"
PIPELINE = f"{USE_CASES}uc/pipeline/"
CHAINS = ("fallback_models", "embedding_fallback_models", "ranking_fallback_models")


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def _catalogue() -> None:
    Model.objects.create(name="gen-1", approved=True, capabilities=["generate"])
    Model.objects.create(name="gen-2", approved=True, capabilities=["generate", "tools"])
    Model.objects.create(name="emb-1", approved=True, capabilities=["embed"])
    Model.objects.create(name="emb-2", approved=True, capabilities=["embed"])
    Model.objects.create(name="rank-1", approved=True, capabilities=["rerank"])
    Model.objects.create(name="speech-1", approved=True, capabilities=["speech"])
    Model.objects.create(name="undeclared-1", approved=True)


def _use_case(admin, *released: str) -> UseCase:
    _client(admin).post(USE_CASES, {"slug": "uc", "name": "UC"}, format="json")
    usecase = UseCase.objects.get(slug="uc")
    usecase.allowed_models.set(Model.objects.filter(name__in=released))
    return usecase


def _everything_released(admin) -> APIClient:
    _catalogue()
    _use_case(admin, *Model.objects.values_list("name", flat=True))
    return _client(admin)


# == what a use case keeps ========================================================================


def test_a_use_case_has_no_fallback_for_any_kind_until_one_is_set() -> None:
    """Empty is the default and an answer: no fallback, the caller's error."""
    admin = _user("admin", "global-admin")
    _use_case(admin)

    resp = _client(admin).get(PIPELINE)

    assert resp.status_code == 200
    assert resp.json() == {"steps": [], **{chain: [] for chain in CHAINS}}


def test_each_kind_keeps_its_own_chain_in_order_and_it_is_announced(captured_events) -> None:
    client = _everything_released(_user("admin", "global-admin"))

    resp = client.put(
        PIPELINE,
        {
            "steps": [],
            "fallback_models": ["gen-2", "gen-1"],
            "embedding_fallback_models": ["emb-2"],
            "ranking_fallback_models": ["rank-1"],
        },
        format="json",
    )

    assert resp.status_code == 200, resp.json()
    stored = PipelineConfig.objects.get(use_case__slug="uc")
    assert (
        stored.fallback_models,
        stored.embedding_fallback_models,
        stored.ranking_fallback_models,
    ) == (["gen-2", "gen-1"], ["emb-2"], ["rank-1"])
    assert client.get(PIPELINE).json()["fallback_models"] == ["gen-2", "gen-1"]
    # The whole pipeline, each chain included: the gateway reads an absent chain as none.
    assert [p for t, p in captured_events if t == "pipeline.upserted"] == [
        {
            "use_case": "uc",
            "steps": [],
            "fallback_models": ["gen-2", "gen-1"],
            "embedding_fallback_models": ["emb-2"],
            "ranking_fallback_models": ["rank-1"],
        }
    ]


def test_an_older_console_that_knows_one_chain_keeps_the_other_two() -> None:
    """A save from a console that sends `fallback_models` alone — and the mode this release no
    longer reads — changes the text chain and leaves embedding and ranking as they were."""
    client = _everything_released(_user("admin", "global-admin"))
    client.put(
        PIPELINE,
        {"steps": [], "fallback_models": [], "embedding_fallback_models": ["emb-1"]},
        format="json",
    )

    resp = client.put(
        PIPELINE,
        {"steps": [], "fallback_models": ["gen-1"], "fallback_mode": "own"},
        format="json",
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json() == {
        "steps": [],
        "fallback_models": ["gen-1"],
        "embedding_fallback_models": ["emb-1"],
        "ranking_fallback_models": [],
        "updated_at": resp.json()["updated_at"],
    }


@pytest.mark.parametrize(
    ("field", "model", "said"),
    [
        ("fallback_models", "emb-1", "Cannot answer a generation request: emb-1"),
        ("fallback_models", "rank-1", "Cannot answer a generation request: rank-1"),
        ("embedding_fallback_models", "gen-1", "Cannot give embeddings: gen-1"),
        ("embedding_fallback_models", "rank-1", "Cannot give embeddings: rank-1"),
        ("ranking_fallback_models", "emb-1", "Cannot rank records: emb-1"),
        ("ranking_fallback_models", "undeclared-1", "Cannot rank records: undeclared-1"),
    ],
)
def test_a_model_of_another_kind_is_refused_by_name_in_the_chain_it_was_put(
    field: str, model: str, said: str
) -> None:
    """The owner's rule: an embedding cannot be answered by a model that writes text, nor the
    other way round — refused where it is typed, under the chain it was typed into."""
    client = _everything_released(_user("admin", "global-admin"))

    resp = client.put(PIPELINE, {"steps": [], field: [model]}, format="json")

    assert resp.status_code == 400
    assert said in str(resp.json()["error"]["details"][field])


@pytest.mark.parametrize(
    ("field", "model"),
    [
        ("fallback_models", "speech-1"),
        ("fallback_models", "undeclared-1"),
        ("embedding_fallback_models", "undeclared-1"),
    ],
)
def test_what_a_model_can_serve_is_read_as_the_gateway_reads_it(field: str, model: str) -> None:
    """A spoken answer is a generation request, so a speech model may stand in for one; a model
    nobody declared counts as generation and embedding (`FRD-114` FR-7)."""
    client = _everything_released(_user("admin", "global-admin"))

    resp = client.put(PIPELINE, {"steps": [], field: [model]}, format="json")

    assert resp.status_code == 200, resp.json()


@pytest.mark.parametrize("where", ["step", "step-chain", "route"])
def test_a_step_names_no_model_that_cannot_write_text(where: str) -> None:
    """Every model a step names classifies, redacts or answers the request — each writes text."""
    client = _everything_released(_user("admin", "global-admin"))
    if where == "step":
        steps = [{"type": "pii_filter", "config": {"model": "emb-1"}}]
    elif where == "step-chain":
        steps = [{"type": "pii_filter", "config": {"model": "gen-1", "fallback_models": ["emb-1"]}}]
    else:
        steps = [
            {
                "type": "model_route",
                "config": {"model": "gen-1", "categories": [{"name": "x", "model": "emb-1"}]},
            }
        ]

    resp = client.put(PIPELINE, {"steps": steps}, format="json")

    assert resp.status_code == 400
    assert "Cannot answer a generation request: emb-1" in str(
        resp.json()["error"]["details"]["steps"]
    )


@pytest.mark.parametrize("field", CHAINS)
@pytest.mark.parametrize(
    ("chain", "said"),
    [
        (["nowhere-1"], "Not released to 'uc': nowhere-1"),
        (["twice-1", "twice-1"], "named twice"),
        ([f"m{n}" for n in range(17)], "At most 16"),
        ([""], "no empty model"),
        ([7], "list of model names"),
        ("gen-1", "list of model names"),
    ],
)
def test_a_chain_names_released_models_once_and_within_bounds(
    field: str, chain: object, said: str
) -> None:
    client = _everything_released(_user("admin", "global-admin"))

    resp = client.put(PIPELINE, {"steps": [], field: chain}, format="json")

    assert resp.status_code == 400
    assert said in str(resp.json())


def test_a_reader_who_may_not_manage_the_use_case_cannot_set_a_chain() -> None:
    _catalogue()
    admin = _user("admin", "global-admin")
    _use_case(admin, "gen-1")

    resp = _client(_user("steuerung", "it-steuerung")).put(
        PIPELINE, {"steps": [], "fallback_models": ["gen-1"]}, format="json"
    )

    assert resp.status_code in (403, 404)
    assert not PipelineConfig.objects.exists()


def test_the_installation_keeps_no_chain_of_its_own() -> None:
    """`ADR-0052`: the Models page's chains are gone, and so is the API they were set through."""
    admin = _client(_user("admin", "global-admin"))

    assert admin.get("/api/v1/fallback-chains/").status_code == 404
    assert admin.put("/api/v1/fallback-chains/generate/", {"models": []}).status_code == 404


# == the migration that removed the installation's chains =========================================

#: The state just before `pipelines/0005`: the installation's chains and a pipeline's mode exist.
BEFORE = [
    ("pipelines", "0004_fallback_mode"),
    ("catalog", "0013_fallback_chains"),
    ("usecases", "0019_mail_on_budgets_and_anomalies"),
    ("outbox", "0004_parked_events_and_a_pending_index"),
]


def _historical_apps():
    """Migrate back to the state before the chains moved, and the models as it had them."""
    call_command("migrate", "pipelines", "0004", verbosity=0)
    return MigrationExecutor(connection).loader.project_state(BEFORE).apps


@pytest.mark.django_db(transaction=True)
def test_the_upgrade_carries_each_installation_chain_into_the_use_cases_that_followed_it(
    settings,
) -> None:
    """**The upgrade, run.** Every use case keeps doing what it did: under `default` it followed
    the installation for every kind, under `own` for embedding and ranking, under `none` for
    nothing — and only as far as the gateway would have tried a model: released to the use case,
    and able to serve the kind. A retired use case is left alone, and every pipeline that changed
    is announced in full."""
    settings.AIRA_KAFKA_STAGE = "t"
    old = _historical_apps()
    model = old.get_model("catalog", "Model")
    gen_1 = model.objects.create(name="gen-1", approved=True, capabilities=["generate"])
    gen_2 = model.objects.create(name="gen-2", approved=True, capabilities=["generate"])
    emb_1 = model.objects.create(name="emb-1", approved=True, capabilities=["embed"])
    rank_1 = model.objects.create(name="rank-1", approved=True, capabilities=["rerank"])
    chain = old.get_model("catalog", "FallbackChain")
    # `emb-1` in the text chain was refused when set; it may have been re-declared since.
    chain.objects.create(kind="generate", chain=["gen-1", "emb-1", "gen-2"])
    chain.objects.create(kind="embed", chain=["emb-1"])
    chain.objects.create(kind="rerank", chain=["rank-1"])
    use_case = old.get_model("usecases", "UseCase")
    pipeline = old.get_model("pipelines", "PipelineConfig")
    followed = use_case.objects.create(slug="followed", name="F")
    followed.allowed_models.set([gen_1, gen_2, emb_1, rank_1])
    pipeline.objects.create(use_case=followed, steps=[], fallback_mode="default")
    no_row = use_case.objects.create(slug="no-row", name="N")
    no_row.allowed_models.set([gen_2])
    own = use_case.objects.create(slug="own", name="O")
    own.allowed_models.set([gen_1, gen_2, emb_1])
    pipeline.objects.create(use_case=own, fallback_models=["gen-2"], fallback_mode="own")
    refused = use_case.objects.create(slug="refused", name="R")
    refused.allowed_models.set([gen_1, emb_1, rank_1])
    pipeline.objects.create(use_case=refused, fallback_mode="none")
    nothing_released = use_case.objects.create(slug="nothing-released", name="X")
    retired = use_case.objects.create(slug="retired", name="T", deleted_at=timezone.now())
    retired.allowed_models.set([gen_1])
    old.get_model("outbox", "OutboxEvent").objects.all().delete()

    call_command("migrate", verbosity=0)

    chains = {
        config.use_case.slug: tuple(getattr(config, field) for field in CHAINS)
        for config in PipelineConfig.objects.select_related("use_case")
    }
    assert chains == {
        "followed": (["gen-1", "gen-2"], ["emb-1"], ["rank-1"]),
        "no-row": (["gen-2"], [], []),
        # Its own text chain kept as it was, the installation's for the other two.
        "own": (["gen-2"], ["emb-1"], []),
        "refused": ([], [], []),
    }
    assert nothing_released.slug not in chains and retired.slug not in chains
    announced = list(OutboxEvent.objects.order_by("id"))
    assert [(e.topic, e.key, e.event_type) for e in announced] == [
        ("aira.t.pipelines", slug, "pipeline.upserted") for slug in ("followed", "no-row", "own")
    ]
    # Field for field what the views send: the consumer reads an absent chain as none.
    for event in announced:
        config = PipelineConfig.objects.get(use_case__slug=event.key)
        assert event.payload == _pipeline_payload(event.key, config)


@pytest.mark.django_db(transaction=True)
def test_an_installation_that_set_no_chain_changes_nothing_on_the_upgrade(settings) -> None:
    """The default: nothing fell back before, nothing is written, nothing is announced."""
    settings.AIRA_KAFKA_STAGE = "t"
    old = _historical_apps()
    gen_1 = old.get_model("catalog", "Model").objects.create(name="gen-1", approved=True)
    followed = old.get_model("usecases", "UseCase").objects.create(slug="followed", name="F")
    followed.allowed_models.set([gen_1])
    old.get_model("pipelines", "PipelineConfig").objects.create(
        use_case=followed, steps=[], fallback_mode="default"
    )
    old.get_model("outbox", "OutboxEvent").objects.all().delete()

    call_command("migrate", verbosity=0)

    config = PipelineConfig.objects.get(use_case__slug="followed")
    assert tuple(getattr(config, field) for field in CHAINS) == ([], [], [])
    assert not OutboxEvent.objects.exists()
