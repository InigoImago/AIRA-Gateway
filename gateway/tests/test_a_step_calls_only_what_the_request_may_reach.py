"""A pipeline step calls only a model the request may reach (`installation_requirements`).

A step's models are checked when the pipeline is saved — against the use case's release, and the
dry run against the installation's approval — and the step's own call applied no condition after
that. So a release withdrawn, or an approval a Global Administrator revoked, after the pipeline was
saved changed nothing: the router went on asking its classifier, and a redactor went on sending
the caller's text — the personal data it exists to withhold — to a model the installation had
withdrawn. Management accepts both changes, and should: refusing to withdraw a model because a
pipeline names it would hold a security decision hostage to a configuration.

So the step asks, per call, what the dispatch chain asks of every candidate: residency, approval,
release and a price where spend is capped. A model it may not call is passed over **with its
reason**, and the step degrades in its own safe direction — the redactor blocks, the LLM filter
falls back to its patterns, the router to its default — saying why, so a withdrawal is not read as a
typo.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

SLUG = "steps"
SERVED = "mock-1"
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)


class _Model:
    """A step's model: **not** a test double, so the installation's conditions apply to it as
    they do to every real model. Answers with what it is told to, and counts its calls."""

    def __init__(self, name: str, answer: str = "SAFE") -> None:
        self._name = name
        self._answer = answer
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        text = self._answer or request.messages[-1].text.replace("Erika", "<PERSON>")
        return CanonicalResponse(model=self._name, text=text, usage=_USAGE)

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _request(text: str = "my name is Erika") -> CanonicalRequest:
    return CanonicalRequest(model=SERVED, messages=[CanonicalMessage(role=Role.USER, text=text)])


def _pipeline(step: StepType, config: dict[str, Any]) -> Pipeline:
    return Pipeline(steps=(PipelineStep(step, config),))


async def _refuses(model: str) -> str | None:
    return "withdrawn" if model == "withdrawn-1" else None


# == the engine asks, and says why ===============================================================


async def test_a_redactor_does_not_send_the_text_to_a_model_it_may_not_call() -> None:
    withdrawn = _Model("withdrawn-1", answer="")
    with pytest.raises(PipelineRejected) as refused:
        await PipelineEngine(ProviderRegistry([withdrawn])).run(
            _pipeline(StepType.PII_FILTER, {"model": "withdrawn-1"}),
            _request(),
            permits=_refuses,
        )

    assert withdrawn.calls == 0
    assert "withdrawn-1: withdrawn" in str(refused.value)


async def test_a_redactor_s_chain_moves_past_a_model_it_may_not_call() -> None:
    """Withheld is not unreachable in what it costs — neither is called — and the chain's next
    model, which the request may reach, redacts."""
    withdrawn, allowed = _Model("withdrawn-1", answer=""), _Model("allowed-1", answer="")
    outcome = await PipelineEngine(ProviderRegistry([withdrawn, allowed])).run(
        _pipeline(StepType.PII_FILTER, {"model": "withdrawn-1", "fallback_models": ["allowed-1"]}),
        _request(),
        permits=_refuses,
    )

    assert (withdrawn.calls, allowed.calls) == (0, 1)
    assert outcome.request.last_user_text() == "my name is <PERSON>"


async def test_an_llm_filter_falls_back_to_its_patterns_and_says_why() -> None:
    withdrawn = _Model("withdrawn-1", answer="INJECTION")
    outcome = await PipelineEngine(ProviderRegistry([withdrawn])).run(
        _pipeline(StepType.INJECTION_FILTER, {"mode": "llm", "model": "withdrawn-1"}),
        _request("what is the weather"),
        permits=_refuses,
    )

    assert withdrawn.calls == 0
    (decision,) = outcome.decisions
    assert decision["action"] != "blocked", decision
    assert decision["why"] == "clean (by the heuristic)", decision


async def test_a_router_uses_its_default_and_says_why() -> None:
    withdrawn = _Model("withdrawn-1", answer="billing")
    pipeline = _pipeline(
        StepType.MODEL_ROUTE,
        {
            "model": "withdrawn-1",
            "categories": [{"name": "billing", "description": "d", "model": "billing-1"}],
            "default_model": "default-1",
        },
    )
    outcome = await PipelineEngine(ProviderRegistry([withdrawn])).run(
        pipeline, _request(), permits=_refuses
    )

    assert withdrawn.calls == 0
    assert outcome.request.model == "default-1"


# == through the gateway, with the installation's own conditions ================================


class _Saved:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(step_model: _Model, pipeline: Pipeline) -> Any:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([*app.state.providers.each(), step_model])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Saved(pipeline)
    return app


async def _setup(app: Any, *, released: list[str], approved: bool = True) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="scrubber", capabilities=["generate"], approved=approved))
        session.add(ModelRead(model=SERVED, capabilities=["generate", "embed"], approved=True))
        session.add(UseCaseRead(slug=SLUG, name=SLUG, allowed_models=released))
        await session.commit()


async def _decisions(app: Any) -> list[dict[str, Any]]:
    async with app.state.db_sessionmaker() as session:
        rows = (await session.execute(select(RequestLog))).scalars()
        return [
            d
            for row in rows
            if not row.operation.startswith("pipeline:")
            for d in row.pipeline_decisions or []
        ]


REDACT = _pipeline(StepType.PII_FILTER, {"model": "scrubber"})
TEXT = {"contents": [{"role": "user", "parts": [{"text": "my name is Erika"}]}]}


async def test_a_released_and_approved_redactor_is_called_as_before() -> None:
    """The control: the conditions refuse what they should and nothing else."""
    scrubber = _Model("scrubber", answer="")
    app = _app(scrubber, REDACT)
    with TestClient(app) as client:
        await _setup(app, released=[SERVED, "scrubber"])
        response = client.post(
            f"/v1beta/models/{SERVED}:generateContent",
            json=TEXT,
            headers={"X-AIRA-Use-Case": SLUG},
        )

    assert response.status_code == 200, response.text
    assert scrubber.calls == 1


@pytest.mark.parametrize(
    ("released", "approved", "reason"),
    [
        pytest.param([SERVED], True, "has not been released", id="release withdrawn"),
        pytest.param([SERVED, "scrubber"], False, "has not been approved", id="approval revoked"),
    ],
)
async def test_a_redactor_whose_model_was_withdrawn_blocks_rather_than_calls_it(
    released: list[str], approved: bool, reason: str
) -> None:
    scrubber = _Model("scrubber", answer="")
    app = _app(scrubber, REDACT)
    with TestClient(app) as client:
        await _setup(app, released=released, approved=approved)
        response = client.post(
            f"/v1beta/models/{SERVED}:generateContent",
            json=TEXT,
            headers={"X-AIRA-Use-Case": SLUG},
        )
        decisions = await _decisions(app)

    assert response.status_code == 400, response.text
    assert scrubber.calls == 0, "the caller's text went to a model the installation withdrew"
    (decision,) = [d for d in decisions if d.get("step") == "pii_filter"]
    assert decision["action"] == "blocked"
    assert "scrubber" in decision["why"] and reason in decision["why"], decision


async def test_the_embedding_path_asks_the_same() -> None:
    """A redactor reaches an embedding too (`FRD-309` FR-9), through its own runner — which is
    the second place the conditions had to be handed in."""
    scrubber = _Model("scrubber", answer="")
    app = _app(scrubber, REDACT)
    with TestClient(app) as client:
        await _setup(app, released=[SERVED])
        response = client.post(
            f"/v1beta/models/{SERVED}:embedContent",
            json={"content": {"parts": [{"text": "my name is Erika"}]}},
            headers={"X-AIRA-Use-Case": SLUG},
        )

    assert response.status_code == 400, response.text
    assert scrubber.calls == 0
