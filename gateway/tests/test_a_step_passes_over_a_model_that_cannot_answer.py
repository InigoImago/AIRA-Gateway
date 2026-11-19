"""A step passes over a model that cannot answer, whatever kept it from answering (`FRD-302` FR-8).

An adapter has four ways of saying it could not answer for a model: the call failed
(`UpstreamError`); the model has nowhere this gateway may send it — no region catalogued, or one
residency forbids (`AmbiguousModel`, `RegionNotAllowed`); or the request asks for something its
wire format cannot carry (`DialectUnsupported`). The dispatch chain passes a candidate over for
each. A step that caught only the first let the other three escape it — past its own chain, past
its safe direction, past the decision that says why. For the filter and the router that was a
refusal of every request of the use case blaming the catalogue, and a dry run answering 500; for
the redactor it was also a refusal whose audit row kept the prompt the step exists to clean.

The chain still moves on for this and nothing else: a model that *answered*, however unhelpfully,
stops it (`test_a_step_asks_a_chain_of_models.py`).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.audit import Outcome
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
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import (
    AmbiguousModel,
    DialectUnsupported,
    ProviderRegistry,
    UpstreamError,
    UpstreamModel,
)
from aira_gateway.upstreams.vertex.adapters import VertexGeminiAdapter
from aira_gateway.upstreams.vertex.transport import VertexTransport

NAME = "Max Mustermann"
PROMPT = f"Bitte sende die Rechnung an {NAME}."

#: What an adapter raises for a model it cannot answer for, one of each kind.
CANNOT_ANSWER = {
    "unreachable": UpstreamError("the model is unavailable", 503),
    "no region": AmbiguousModel("'primary-1' is catalogued for this platform and says no region."),
    "region not permitted": RegionNotAllowed("Region 'us-central1' is not permitted."),
    "unsayable": DialectUnsupported("This dialect cannot express the request."),
}


class _CannotAnswer:
    """A model whose adapter raises ``error`` for every call — the real exception classes."""

    def __init__(self, name: str, error: Exception) -> None:
        self._name = name
        self._error = error
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        raise self._error

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Answers(_CannotAnswer):
    """A model that answers each step as a compliant one would: one word for a classifier, a
    rewrite for a redactor."""

    def __init__(self, name: str) -> None:
        super().__init__(name, RuntimeError("unused"))

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        system, text = request.messages[0].text, request.messages[-1].text
        if "security classifier" in system:
            answer = "SAFE"
        elif "routing classifier" in system:
            answer = "code"
        else:
            answer = text.replace(NAME, "<PERSON>")
        return CanonicalResponse(
            model=self._name,
            text=answer,
            usage=CanonicalUsage(prompt_tokens=7, completion_tokens=3),
        )


def _request() -> CanonicalRequest:
    return CanonicalRequest(
        model="served-1", messages=[CanonicalMessage(role=Role.USER, text=PROMPT)]
    )


def _step(kind: str, **extra: Any) -> Pipeline:
    configs: dict[str, dict[str, Any]] = {
        "injection_filter": {"mode": "llm"},
        "model_route": {
            "default_model": "general-1",
            "categories": [{"name": "code", "model": "coder-1"}],
        },
        "pii_filter": {},
    }
    return Pipeline(
        steps=(PipelineStep(StepType(kind), {**configs[kind], "model": "primary-1", **extra}),)
    )


#: The decision each step leaves when the model after the one that could not answer did.
ANSWERED_BY_THE_BACKUP = {
    "injection_filter": {
        "step": "injection_filter",
        "flagged": False,
        "action": "block",
        "why": "clean",
    },
    "model_route": {"step": "model_route", "category": "code", "from": "served-1", "to": "coder-1"},
    "pii_filter": {"step": "pii_filter", "action": "redacted", "why": "backup-1"},
}


@pytest.mark.parametrize("why", sorted(CANNOT_ANSWER))
@pytest.mark.parametrize("kind", sorted(ANSWERED_BY_THE_BACKUP))
async def test_the_chain_moves_past_a_model_that_cannot_answer(kind: str, why: str) -> None:
    """The step's own chain is the somewhere-else this exists for: the backup answers, and the
    decision and the bill both name it."""
    primary, backup = _CannotAnswer("primary-1", CANNOT_ANSWER[why]), _Answers("backup-1")
    engine = PipelineEngine(ProviderRegistry([primary, backup]))

    outcome = await engine.run(_step(kind, fallback_models=["backup-1"]), _request())

    assert (primary.calls, backup.calls) == (1, 1)
    assert outcome.decisions == [ANSWERED_BY_THE_BACKUP[kind]]
    assert [call.model for call in outcome.model_calls] == ["backup-1"]


@pytest.mark.parametrize("why", sorted(CANNOT_ANSWER))
async def test_a_filter_no_model_can_answer_decides_on_undetermined(why: str) -> None:
    """Its safe direction: undetermined, which blocks unless the operator chose otherwise — and
    the refusal says the filter could not check, not that the catalogue is wrong."""
    engine = PipelineEngine(ProviderRegistry([_CannotAnswer("primary-1", CANNOT_ANSWER[why])]))

    with pytest.raises(PipelineRejected, match="could not reach a verdict"):
        await engine.run(_step("injection_filter"), _request())
    allowed = await engine.run(_step("injection_filter", on_undetermined="allow"), _request())

    assert allowed.decisions[0]["why"] == "undetermined"


@pytest.mark.parametrize("why", sorted(CANNOT_ANSWER))
async def test_a_router_no_model_can_answer_takes_its_default(why: str) -> None:
    engine = PipelineEngine(ProviderRegistry([_CannotAnswer("primary-1", CANNOT_ANSWER[why])]))

    outcome = await engine.run(_step("model_route"), _request())

    assert outcome.request.model == "general-1"
    assert outcome.decisions == [
        {"step": "model_route", "action": "not_asked", "why": "classifier_failed"}
    ]


@pytest.mark.parametrize("why", sorted(CANNOT_ANSWER))
async def test_a_redactor_no_model_can_answer_blocks_and_says_why(why: str) -> None:
    error = CANNOT_ANSWER[why]
    engine = PipelineEngine(ProviderRegistry([_CannotAnswer("primary-1", error)]))

    with pytest.raises(PipelineRejected, match="Personal data could not be removed") as refused:
        await engine.run(_step("pii_filter"), _request())

    reason = error.message if isinstance(error, UpstreamError) else str(error)
    assert f"the redactor could not be reached ({reason})" in refused.value.message


# == end to end: the row a refusal leaves, and the dry run ========================================


REGION = "europe-west1"


def _vertex() -> VertexGeminiAdapter:
    """The real Vertex adapter, with nothing configured — so a model catalogued without a region
    is one it raises `AmbiguousModel` for, as it would in production."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover — never reached
        raise AssertionError(f"a model with no region was sent a request: {request.url}")

    return VertexGeminiAdapter(
        VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            allowed_regions=(REGION,),
        ),
        [],
    )


def _app(step: dict[str, Any]):  # noqa: ANN202
    app = create_app(
        GatewaySettings(
            auth_required=False, require_use_case=False, log_queue_size=0, allowed_regions=REGION
        )
    )
    registry = ProviderRegistry([_vertex()])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction.
    app.state.pipeline_engine = PipelineEngine(registry)

    class _Store:
        async def get(self, use_case: object) -> Pipeline:
            return Pipeline.from_dict({"steps": [step]})

    app.state.pipeline_store = _Store()
    return app


async def _catalogue_without_a_region(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", allowed_models=None))
        session.add(
            ModelRead(
                model="gemini-guard",
                capabilities=["generate"],
                provider="vertex",
                publisher="google",
                addressing={},
            )
        )
        await session.commit()


async def test_a_redactor_that_cannot_be_addressed_refuses_and_keeps_no_prompt() -> None:
    """The refusal is the step's, and its row is the step's too: the decision says the redactor
    could not be reached, and the stored body is dropped (`FRD-309` FR-3). Escaping the step, the
    same refusal kept the caller's prompt — name and address — on a row nobody was served."""
    app = _app({"type": "pii_filter", "config": {"model": "gemini-guard"}})
    with TestClient(app) as client:
        await _catalogue_without_a_region(app)
        response = client.post(
            "/v1beta/models/gemini-guard:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": PROMPT}]}]},
        )
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 400, response.text
    assert "Personal data could not be removed" in response.json()["error"]["message"]
    caller = [row for row in rows if not row.operation.startswith("pipeline:")]
    assert [row.outcome for row in caller] == [Outcome.BLOCKED_BY_PIPELINE]
    assert [row.request_payload for row in caller] == [None]
    decisions = caller[0].pipeline_decisions
    assert decisions is not None and decisions[0]["action"] == "blocked"


async def test_the_dry_run_of_a_filter_that_cannot_be_addressed_shows_the_outcome() -> None:
    """The builder's preview answers with what production would do — refuse, undetermined —
    instead of a 500 that tells the operator nothing about the step."""
    app = _app({"type": "injection_filter", "config": {"mode": "llm", "model": "gemini-guard"}})
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue_without_a_region(app)
        response = client.post(
            "/v1beta/pipeline:dryRun",
            json={
                "use_case": "uc",
                "user": "what is 2+2?",
                "model": "gemini-guard",
                "pipeline": {
                    "steps": [
                        {
                            "type": "injection_filter",
                            "config": {"mode": "llm", "model": "gemini-guard"},
                        }
                    ]
                },
            },
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["blocked"] is True
    assert [(entry["action"], entry["detail"]["verdict"]) for entry in result["trace"]] == [
        ("blocked", "undetermined")
    ]
