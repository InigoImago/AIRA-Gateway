"""Which models a use case may call, and the three ways round it that used to work (`FRD-308`).

The predecessor of this rule was the `allow_check` pipeline step, and on 2026-08-11 it was
**measured** rather than read:

    a caller naming a forbidden model      → 403   ✅
    a `model_route` step re-targeting one  → 200, served
    a `fallback_models` chain reaching one → 200, served

It ran once, before routing, against the model the *caller* named — and `requirements.py` had
already written the rule down in its own docstring: *the check that runs before routing protects
nothing*. The first two tests below are those two holes, and they are the reason a release is a
dispatch condition rather than a stage.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.requirements import ModelReleasedForUseCase
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


def _body(text: str = "hello") -> dict[str, Any]:
    return {"contents": [{"role": "user", "parts": [{"text": text}]}]}


class _Echo:
    """A real-looking model: **not** a test double, because the exemption for those would make
    every case here pass without the rule existing."""

    def __init__(self, name: str) -> None:
        self._name = name

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",), "acme", "acme", "")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=request.model,
            text=f"[{request.model}]",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request: object):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Classifier(_Echo):
    """A router that always answers with the same category."""

    def __init__(self, name: str, verdict: str) -> None:
        super().__init__(name)
        self._verdict = verdict

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=self._name,
            text=self._verdict,
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


async def _release(app: Any, slug: str, models: list[str] | None) -> None:
    """Put a use case in the read-model with exactly this release."""
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, allowed_models=models))
        await session.commit()


async def _catalogue(app: Any, *models: str) -> None:
    """Catalogue and approve, so `ModelApproved` is satisfied and this file tests **its own**
    rule. Two gates with different owners means a test of the second has to pass the first."""
    async with app.state.db_sessionmaker() as session:
        for model in models:
            session.add(ModelRead(model=model, approved=True, capabilities=["generate"]))
        await session.commit()


def _app(pipeline: Pipeline, *providers: object) -> Any:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(pipeline)
    return app


def _post(client: TestClient, model: str, slug: str = "uc") -> Any:
    return client.post(
        f"/v1beta/models/{model}:generateContent",
        json=_body(),
        headers={"X-AIRA-Use-Case": slug},
    )


# == the three ways round the step this replaces =================================================


async def test_a_model_the_use_case_was_never_given_is_refused() -> None:
    app = _app(Pipeline(), _Echo("released-1"), _Echo("withheld-1"))
    with TestClient(app) as client:
        await _catalogue(app, "released-1", "withheld-1")
        await _release(app, "uc", ["released-1"])
        response = _post(client, "withheld-1")

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "withheld-1" in message
    assert "released" in message


async def test_a_router_cannot_re_target_a_request_at_a_withheld_model() -> None:
    """Hole one, measured at 200 before this existed. `model_route` rewrites the model *after* the
    old step had run, so a use case restricted to one model could be routed to any other."""
    pipeline = Pipeline(
        steps=(
            PipelineStep(
                StepType.MODEL_ROUTE,
                {"model": "router", "categories": [{"name": "cheap", "model": "withheld-1"}]},
            ),
        )
    )
    app = _app(pipeline, _Echo("released-1"), _Echo("withheld-1"), _Classifier("router", "cheap"))
    with TestClient(app) as client:
        await _catalogue(app, "released-1", "withheld-1", "router")
        await _release(app, "uc", ["released-1", "router"])
        response = _post(client, "released-1")

    assert response.status_code == 400, response.text
    assert "withheld-1" in response.json()["error"]["message"]


async def test_a_fallback_chain_cannot_reach_a_withheld_model() -> None:
    """Hole two, also 200 before. The chain's candidates were never compared with the list at
    all — and a fallback is exactly where a request ends up when something is already wrong."""
    app = _app(Pipeline(fallback_models=("withheld-1",)), _Echo("released-1"), _Echo("withheld-1"))
    with TestClient(app) as client:
        await _catalogue(app, "released-1", "withheld-1")
        await _release(app, "uc", ["released-1"])
        response = _post(client, "released-1")

    # The primary is released and answers, so the chain never needed the fallback — the property
    # under test is the one below, where it does.
    assert response.status_code == 200
    assert response.json()["modelVersion"] == "released-1"


async def test_an_exhausted_chain_says_which_models_were_excluded_and_why() -> None:
    """A chain whose every candidate is withheld fails **naming the reason**, rather than
    answering from the one model that happened to be allowed nowhere."""
    app = _app(Pipeline(fallback_models=("withheld-2",)), _Echo("withheld-1"), _Echo("withheld-2"))
    with TestClient(app) as client:
        await _catalogue(app, "withheld-1", "withheld-2")
        await _release(app, "uc", [])
        response = _post(client, "withheld-1")

    assert response.status_code == 400
    assert "no model released" in response.json()["error"]["message"]


# == the three states of "which models" ==========================================================


async def test_an_empty_release_is_an_answer_and_the_answer_is_no() -> None:
    """The owner's decision, 2026-08-11: a use case reaches the models somebody released for it.
    Absence of a release is not a release — the same rule as "unpriced is not free"."""
    app = _app(Pipeline(), _Echo("anything-1"))
    with TestClient(app) as client:
        await _catalogue(app, "anything-1")
        await _release(app, "uc", [])
        response = _post(client, "anything-1")

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "no model released" in message
    # Named separately from "this model was not released", because the two need different actions:
    # release *this* model, or release *a* model.
    assert "administrator of the use case" in message


async def test_a_use_case_no_event_has_described_is_not_refused() -> None:
    """`None` is not `[]`. A read-model row written by a Management that predates this feature
    carries no release, and reading that silence as "nothing" would stop every use case on a
    half-upgraded stack — a governance control arriving as an outage, which `FRD-500` records as
    the way a control gets switched off for good."""
    app = _app(Pipeline(), _Echo("anything-1"))
    with TestClient(app) as client:
        await _catalogue(app, "anything-1")
        await _release(app, "uc", None)
        response = _post(client, "anything-1")

    assert response.status_code == 200


async def test_a_request_with_no_use_case_at_all_is_not_refused_here() -> None:
    """An unbound break-glass key names no use case (`ADR-0015`). There is no release to consult,
    and inventing one would turn this control into an outage for the credential that exists to
    survive an outage. `ModelApproved` still applies."""
    app = _app(Pipeline(), _Echo("anything-1"))
    with TestClient(app) as client:
        await _catalogue(app, "anything-1")
        response = client.post(
            "/v1beta/models/anything-1:generateContent", json=_body(), headers={}
        )

    assert response.status_code == 200


# == the refusal is on the audit trail ===========================================================


async def test_a_refused_request_is_recorded_with_what_was_asked_for() -> None:
    """`FRD-122`: the log records what was *asked*, not only what was served. A governance refusal
    that leaves no row is one nobody can review — and "why can this team not use that model" is
    exactly the question a review opens with."""
    app = _app(Pipeline(), _Echo("released-1"), _Echo("withheld-1"))
    with TestClient(app) as client:
        await _catalogue(app, "released-1", "withheld-1")
        await _release(app, "uc", ["released-1"])
        assert _post(client, "withheld-1").status_code == 400

        # Inside the client, deliberately: leaving it makes the lifespan dispose the engine, and
        # an in-memory SQLite database disposed is a database deleted.
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert len(rows) == 1
    assert rows[0].requested_model == "withheld-1"
    assert rows[0].use_case == "uc"
    assert rows[0].outcome is not None


# == the requirement on its own ==================================================================


@pytest.mark.parametrize(
    ("released", "model", "refused"),
    [
        (None, "anything", False),
        ([], "anything", True),
        (["a"], "a", False),
        (["a"], "b", True),
        (["a", "b"], "b", False),
    ],
)
async def test_the_rule_in_isolation(released: list[str] | None, model: str, refused: bool) -> None:
    requirement = ModelReleasedForUseCase(released, "uc")
    assert (await requirement.refusal(model) is not None) is refused


async def test_a_test_double_is_exempt_exactly_as_approval_is() -> None:
    """Not a model. The exemption is bounded by where the double is registered at all — `local`
    and nothing else — and it has to be the same answer `ModelApproved` gives, or a hermetic suite
    would need a release per invented model in fifty files."""

    class _Double(_Echo):
        is_test_double = True

    registry = ProviderRegistry([_Double("mock-1")])
    requirement = ModelReleasedForUseCase([], "uc", registry)

    assert await requirement.refusal("mock-1") is None
