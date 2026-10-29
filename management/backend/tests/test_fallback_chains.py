"""The installation's fallback chain per kind of request, and a use case's choice about it.

`FRD-310`.

A chain per kind — text generation, embedding, ranking — empty until somebody with `catalog.write`
sets one: a model that cannot answer is then the caller's error, which is the default. A chain
names only models that can serve its kind, so an embedding never falls back to a model that writes
text. A use case follows the installation's chains, keeps its own for text generation, or has none.
"""

from __future__ import annotations

import importlib

import pytest
from aira_management.apps.catalog.models import FallbackChain, Model
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.apps import apps
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

CHAINS = "/api/v1/fallback-chains/"
USE_CASES = "/api/v1/use-cases/"


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
    Model.objects.create(name="rank-1", approved=True, capabilities=["rerank"])
    Model.objects.create(name="speech-1", approved=True, capabilities=["speech"])
    Model.objects.create(name="withheld-1", approved=False, capabilities=["generate"])


# == the installation's chains ====================================================================


def test_every_kind_has_a_chain_and_each_starts_empty() -> None:
    """Empty is the default and an answer: no fallback, the caller's error."""
    resp = _client(_user("member")).get(CHAINS)

    assert resp.status_code == 200
    assert resp.json() == {"generate": [], "embed": [], "rerank": []}


def test_a_chain_is_set_in_order_and_announced(captured_events) -> None:
    _catalogue()

    resp = _client(_user("admin", "global-admin")).put(
        f"{CHAINS}generate/", {"models": ["gen-2", "gen-1"]}, format="json"
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["generate"] == ["gen-2", "gen-1"]
    assert FallbackChain.objects.get(kind="generate").chain == ["gen-2", "gen-1"]
    assert captured_events[-1] == (
        "fallback_chain.upserted",
        {"kind": "generate", "models": ["gen-2", "gen-1"]},
    )


def test_an_empty_chain_is_announced_too(captured_events) -> None:
    """It is what takes the last fallback away on the gateway."""
    _catalogue()
    client = _client(_user("admin", "global-admin"))
    client.put(f"{CHAINS}embed/", {"models": ["emb-1"]}, format="json")

    client.put(f"{CHAINS}embed/", {"models": []}, format="json")

    assert captured_events[-1] == ("fallback_chain.upserted", {"kind": "embed", "models": []})


def test_only_catalog_write_may_set_a_chain() -> None:
    """A chain decides which model answers every use case that follows it, as approving does."""
    _catalogue()

    resp = _client(_user("steuerung", "it-steuerung")).put(
        f"{CHAINS}generate/", {"models": ["gen-1"]}, format="json"
    )

    assert resp.status_code == 403
    assert not FallbackChain.objects.exists()


@pytest.mark.parametrize(
    ("kind", "model", "said"),
    [
        ("embed", "gen-1", "does not declare embedding"),
        ("generate", "emb-1", "does not declare text generation"),
        ("generate", "rank-1", "does not declare text generation"),
        ("rerank", "emb-1", "does not declare ranking"),
    ],
)
def test_a_model_of_another_kind_is_refused_by_name(kind: str, model: str, said: str) -> None:
    """The owner's rule: an embedding cannot be answered by a model that writes text, nor the
    other way round."""
    _catalogue()

    resp = _client(_user("admin", "global-admin")).put(
        f"{CHAINS}{kind}/", {"models": [model]}, format="json"
    )

    assert resp.status_code == 400
    assert said in str(resp.json())
    assert model in str(resp.json())


def test_a_speech_model_may_stand_in_for_a_spoken_answer() -> None:
    """A spoken answer is a generation request; the gateway gives a speech model only the requests
    that want speech (`SpeechSupported`)."""
    _catalogue()

    resp = _client(_user("admin", "global-admin")).put(
        f"{CHAINS}generate/", {"models": ["speech-1"]}, format="json"
    )

    assert resp.status_code == 200, resp.json()


@pytest.mark.parametrize(
    ("models", "said"),
    [
        (["nowhere-1"], "not in the catalogue"),
        (["withheld-1"], "not approved"),
        (["gen-1", "gen-1"], "named twice"),
        ([f"m{n}" for n in range(17)], "16"),
    ],
)
def test_a_chain_names_catalogued_approved_models_once_and_within_bounds(
    models: list[str], said: str
) -> None:
    _catalogue()

    resp = _client(_user("admin", "global-admin")).put(
        f"{CHAINS}generate/", {"models": models}, format="json"
    )

    assert resp.status_code == 400
    assert said in str(resp.json())


def test_a_kind_that_does_not_exist_is_a_404() -> None:
    resp = _client(_user("admin", "global-admin")).put(
        f"{CHAINS}vision/", {"models": []}, format="json"
    )

    assert resp.status_code == 404


def test_a_model_removed_from_the_catalogue_leaves_every_chain(captured_events) -> None:
    """A chain naming a model nobody catalogues would be skipped at every hop and shown here as
    configured."""
    _catalogue()
    client = _client(_user("admin", "global-admin"))
    client.put(f"{CHAINS}generate/", {"models": ["gen-1", "gen-2"]}, format="json")

    resp = client.delete("/api/v1/models/gen-1/")

    assert resp.status_code == 204
    assert FallbackChain.objects.get(kind="generate").chain == ["gen-2"]
    assert ("fallback_chain.upserted", {"kind": "generate", "models": ["gen-2"]}) in (
        captured_events
    )


# == a use case's choice ==========================================================================


def _use_case(admin, *released: str) -> UseCase:
    _client(admin).post(USE_CASES, {"slug": "uc", "name": "UC"}, format="json")
    usecase = UseCase.objects.get(slug="uc")
    usecase.allowed_models.set(Model.objects.filter(name__in=released))
    return usecase


def test_a_use_case_sees_the_chain_each_kind_of_its_requests_meets(captured_events) -> None:
    _catalogue()
    admin = _user("admin", "global-admin")
    client = _client(admin)
    client.put(f"{CHAINS}generate/", {"models": ["gen-1"]}, format="json")
    client.put(f"{CHAINS}embed/", {"models": ["emb-1"]}, format="json")
    _use_case(admin, "gen-1", "gen-2", "emb-1")

    followed = client.get(f"{USE_CASES}uc/pipeline/").json()
    own = client.put(
        f"{USE_CASES}uc/pipeline/",
        {"steps": [], "fallback_models": ["gen-2"], "fallback_mode": "own"},
        format="json",
    ).json()
    none = client.put(
        f"{USE_CASES}uc/pipeline/",
        {"steps": [], "fallback_models": [], "fallback_mode": "none"},
        format="json",
    ).json()

    assert followed["fallbacks"] == {"generate": ["gen-1"], "embed": ["emb-1"], "rerank": []}
    assert own["fallbacks"] == {"generate": ["gen-2"], "embed": ["emb-1"], "rerank": []}
    assert none["fallbacks"] == {"generate": [], "embed": [], "rerank": []}
    assert [p["fallback_mode"] for t, p in captured_events if t == "pipeline.upserted"] == [
        "own",
        "none",
    ]


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"fallback_models": [], "fallback_mode": "own"}, "at least one model"),
        ({"fallback_models": ["gen-1"], "fallback_mode": "default"}, "only when it keeps one"),
        ({"fallback_models": ["gen-1"], "fallback_mode": "none"}, "only when it keeps one"),
        ({"fallback_models": [], "fallback_mode": "sometimes"}, "valid choice"),
    ],
)
def test_a_mode_and_a_chain_must_agree(body: dict, said: str) -> None:
    """A chain of its own is used under `own` alone: kept under another mode, it would be shown as
    configured and used by nothing."""
    _catalogue()
    admin = _user("admin", "global-admin")
    _use_case(admin, "gen-1")

    resp = _client(admin).put(f"{USE_CASES}uc/pipeline/", {"steps": [], **body}, format="json")

    assert resp.status_code == 400
    assert said in str(resp.json())


def test_a_save_that_says_no_mode_means_what_such_a_pipeline_always_meant() -> None:
    """An older console sends no mode: its own chain where it names one."""
    _catalogue()
    admin = _user("admin", "global-admin")
    _use_case(admin, "gen-1")
    client = _client(admin)

    client.put(
        f"{USE_CASES}uc/pipeline/", {"steps": [], "fallback_models": ["gen-1"]}, format="json"
    )
    with_chain = PipelineConfig.objects.get(use_case__slug="uc").fallback_mode
    client.put(f"{USE_CASES}uc/pipeline/", {"steps": [], "fallback_models": []}, format="json")
    without = PipelineConfig.objects.get(use_case__slug="uc").fallback_mode

    assert (with_chain, without) == ("own", "default")


@pytest.mark.parametrize("where", ["chain", "step", "step-chain", "route"])
def test_a_pipeline_names_no_model_that_cannot_write_text(where: str) -> None:
    """Every model a pipeline names classifies, redacts, or answers the request — each writes
    text. An embedding model anywhere in it is refused by name where it is typed."""
    _catalogue()
    admin = _user("admin", "global-admin")
    _use_case(admin, "gen-1", "emb-1")
    body: dict = {"steps": [], "fallback_models": [], "fallback_mode": "default"}
    if where == "chain":
        body.update(fallback_models=["emb-1"], fallback_mode="own")
    elif where == "step":
        body["steps"] = [{"type": "pii_filter", "config": {"model": "emb-1"}}]
    elif where == "step-chain":
        body["steps"] = [
            {"type": "pii_filter", "config": {"model": "gen-1", "fallback_models": ["emb-1"]}}
        ]
    else:
        body["steps"] = [
            {
                "type": "model_route",
                "config": {"model": "gen-1", "categories": [{"name": "x", "model": "emb-1"}]},
            }
        ]

    resp = _client(admin).put(f"{USE_CASES}uc/pipeline/", body, format="json")

    assert resp.status_code == 400
    assert "emb-1" in str(resp.json())
    assert "generation" in str(resp.json())


def test_the_migration_keeps_what_each_pipeline_did() -> None:
    """A pipeline with a chain of its own goes on using it; one without follows the installation's
    chains, which are empty — so nothing falls back that did not before."""
    _catalogue()
    admin = _user("admin", "global-admin")
    usecase = _use_case(admin, "gen-1")
    other = UseCase.objects.create(slug="other", name="Other")
    PipelineConfig.objects.create(use_case=usecase, fallback_models=["gen-1"])
    PipelineConfig.objects.create(use_case=other)
    migration = importlib.import_module(
        "aira_management.apps.pipelines.migrations.0004_fallback_mode"
    )

    migration.keep_what_each_pipeline_did(apps, None)

    modes = dict(PipelineConfig.objects.values_list("use_case__slug", "fallback_mode"))
    assert modes == {"uc": "own", "other": "default"}
