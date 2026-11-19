"""A pipeline step asks its model the way a dispatch asks it (`FRD-507`, `FRD-145`).

A step's call is a model access like any other, and the catalogue says what reaching the model
takes besides its name. **Where** it is served: on a regional platform a model reached by being
catalogued — the ordinary case, since cataloguing a model is enough to serve it — has no address
but the regions its entry lists, and an alias's wire name is in the same block. And whether it
**samples at its own settings**: Anthropic's current Claude refuses every temperature but its own.
The dispatch chain reads both off the declaration for every candidate. A step that did not had
nowhere to send its call, or was refused by the vendor on every call, and either way the step's
failure became every request's: the filter refused the use case's traffic, the redactor blocked it
— or, under `on_failure: allow`, sent it unredacted.

Each case drives the real Vertex adapter over a fake transport. The adapter is what decides where
a request goes and what it carries, so a stand-in that accepted anything would pass whatever the
step sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.db.models import ModelRead, UseCaseRead
from aira_gateway.pipeline.classifiers import REDACTION_TEMPERATURE
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter
from aira_gateway.upstreams.vertex.regions import VertexModel
from aira_gateway.upstreams.vertex.transport import VertexTransport

#: A step's model, catalogued and named nowhere in `AIRA_VERTEX_MODELS`.
GUARD = "gemini-guard"
REGION = "europe-west1"
HOST = f"{REGION}-aiplatform.googleapis.com"
CLAUDE = "claude-sonnet-5-5"
PROMPT = "Bitte sende die Rechnung an Max Mustermann, Hauptstrasse 3, 12345 Berlin."
REDACTED = "Bitte sende die Rechnung an <PERSON>, <ADDRESS>."


def _gemini_answer(text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1, "totalTokenCount": 11},
    }


def _transport(handler: Any) -> VertexTransport:
    return VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allowed_regions=(REGION,),
    )


def _vertex(contacted: list[httpx.URL]) -> VertexGeminiAdapter:
    """Vertex with **nothing** configured: every model is reached by being catalogued. It answers
    each step as a compliant model would — one word for a classifier, a rewrite for a redactor."""

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url)
        body = json.loads(request.content)
        system = " ".join(
            part["text"] for part in body.get("systemInstruction", {}).get("parts", [])
        )
        text = body["contents"][-1]["parts"][0]["text"]
        if "security classifier" in system:
            answer = "SAFE"
        elif "routing classifier" in system:
            answer = "code"
        else:
            answer = text.replace("Max Mustermann", "<PERSON>").replace(
                "Hauptstrasse 3, 12345 Berlin", "<ADDRESS>"
            )
        return httpx.Response(200, json=_gemini_answer(answer))

    return VertexGeminiAdapter(_transport(handler), [])


def _catalogued(**addressing: Any) -> Any:
    """The catalogue's declaration of every model a step names: Vertex, Google's dialect, and the
    addressing given — the regions alone unless a case says otherwise."""

    async def declaration_of(model: str) -> ModelDeclaration:
        return ModelDeclaration(
            name=model,
            declared=True,
            in_catalog=True,
            provider="vertex",
            publisher="google",
            addressing=addressing or {"regions": [REGION]},
        )

    return declaration_of


def _request(text: str = PROMPT) -> CanonicalRequest:
    return CanonicalRequest(
        model="served-1", messages=[CanonicalMessage(role=Role.USER, text=text)]
    )


#: Each step that calls a model of its own, and the decision it leaves when that model answered.
STEPS = {
    "injection_filter": (
        PipelineStep(StepType.INJECTION_FILTER, {"mode": "llm", "model": GUARD}),
        {"step": "injection_filter", "flagged": False, "action": "block", "why": "clean"},
    ),
    "model_route": (
        PipelineStep(
            StepType.MODEL_ROUTE,
            {
                "model": GUARD,
                "default_model": "general-1",
                "categories": [{"name": "code", "model": "coder-1"}],
            },
        ),
        {"step": "model_route", "category": "code", "from": "served-1", "to": "coder-1"},
    ),
    "pii_filter": (
        PipelineStep(StepType.PII_FILTER, {"model": GUARD}),
        {"step": "pii_filter", "action": "redacted", "why": GUARD},
    ),
}


@pytest.mark.parametrize("kind", sorted(STEPS))
async def test_each_step_reaches_a_catalogued_model_where_the_catalogue_places_it(
    kind: str,
) -> None:
    """The case every installation that catalogues its step models is in. Without the addressing
    the adapter has no region to send the call to and raises, and the step never asks at all."""
    step, decided = STEPS[kind]
    contacted: list[httpx.URL] = []
    engine = PipelineEngine(ProviderRegistry([_vertex(contacted)]))

    outcome = await engine.run(Pipeline(steps=(step,)), _request(), declaration_of=_catalogued())

    assert [url.host for url in contacted] == [HOST]
    assert outcome.decisions == [decided]


async def test_a_step_sends_the_wire_name_the_catalogue_gives_its_model() -> None:
    """The catalogue's addressing carries an alias's wire name as well as its regions: the step
    sends the whole block, as dispatch does, rather than picking out the half it thought of."""
    contacted: list[httpx.URL] = []
    engine = PipelineEngine(ProviderRegistry([_vertex(contacted)]))
    filtering = Pipeline(steps=(STEPS["injection_filter"][0],))

    await engine.run(
        filtering,
        _request("what is 2+2?"),
        declaration_of=_catalogued(regions=[REGION], model="gemini-2.5-flash"),
    )

    assert [url.host for url in contacted] == [HOST]
    assert "/models/gemini-2.5-flash:generateContent" in contacted[0].path


# == end to end: the request and the dry run ======================================================


def _app(contacted: list[httpx.URL]):  # noqa: ANN202
    app = create_app(
        GatewaySettings(
            auth_required=False, require_use_case=False, log_queue_size=0, allowed_regions=REGION
        )
    )
    registry = ProviderRegistry([_vertex(contacted)])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction.
    app.state.pipeline_engine = PipelineEngine(registry)

    class _Store:
        async def get(self, use_case: object) -> Pipeline:
            return Pipeline(steps=(STEPS["injection_filter"][0],))

    app.state.pipeline_store = _Store()
    return app


async def _catalogue(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", allowed_models=None))
        session.add(
            ModelRead(
                model=GUARD,
                capabilities=["generate"],
                provider="vertex",
                publisher="google",
                addressing={"regions": [REGION]},
            )
        )
        await session.commit()


async def test_a_request_through_a_catalogued_filter_is_served() -> None:
    """The caller asks the very model the filter uses, catalogued in the same region. Dispatch
    reached it all along; the filter's own call is what failed, and with it every request."""
    contacted: list[httpx.URL] = []
    app = _app(contacted)
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"/v1beta/models/{GUARD}:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "what is 2+2?"}]}]},
        )

    assert response.status_code == 200, response.text
    # The filter's call, then the caller's own — both in the region the catalogue names.
    assert [url.host for url in contacted] == [HOST, HOST]


async def test_the_dry_run_of_a_catalogued_filter_answers_with_its_verdict() -> None:
    contacted: list[httpx.URL] = []
    app = _app(contacted)
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = client.post(
            "/v1beta/pipeline:dryRun",
            json={
                "use_case": "uc",
                "user": "what is 2+2?",
                "model": GUARD,
                "pipeline": {
                    "steps": [
                        {"type": "injection_filter", "config": {"mode": "llm", "model": GUARD}}
                    ]
                },
            },
        )

    assert response.status_code == 200, response.text
    assert [entry["detail"]["verdict"] for entry in response.json()["trace"]] == ["clean"]


# == sampling: the redactor's temperature =========================================================


def _anthropic_events(text: str) -> bytes:
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {"id": "m", "role": "assistant", "usage": {"input_tokens": 30}},
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _claude(sent: list[dict[str, Any]], *, fixed_sampling: bool = True) -> VertexAnthropicAdapter:
    """Claude on Vertex, answering as `FRD-145` recorded Anthropic does: a model that samples at its
    own settings with a 400 for any temperature but 1, and a redaction otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if fixed_sampling and body.get("temperature") not in (None, 1):
            return httpx.Response(
                400,
                json={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "temperature: not supported for this model",
                    },
                },
            )
        return httpx.Response(
            200,
            content=_anthropic_events(REDACTED),
            headers={"content-type": "text/event-stream"},
        )

    return VertexAnthropicAdapter(
        _transport(handler),
        [VertexModel(REGION, "anthropic", CLAUDE)],
        default_max_tokens=1024,
    )


def _claude_declared(*, fixed_sampling: bool) -> Any:
    async def declaration_of(model: str) -> ModelDeclaration:
        return ModelDeclaration(
            name=model,
            declared=True,
            in_catalog=True,
            provider="vertex",
            publisher="anthropic",
            fixed_sampling=fixed_sampling,
        )

    return declaration_of


async def test_a_redactor_on_a_model_that_samples_at_its_own_settings_redacts() -> None:
    """Sent the redactor's temperature, such a model refuses every call, and a redactor that cannot
    be asked blocks every request of its use case — or, under `on_failure: allow`, lets every one
    through as typed. Sent none, it redacts."""
    sent: list[dict[str, Any]] = []
    engine = PipelineEngine(ProviderRegistry([_claude(sent)]))
    pipeline = Pipeline(steps=(PipelineStep(StepType.PII_FILTER, {"model": CLAUDE}),))

    outcome = await engine.run(
        pipeline, _request(), declaration_of=_claude_declared(fixed_sampling=True)
    )

    assert outcome.request.messages[-1].text == REDACTED
    assert [body.get("temperature") for body in sent] == [None]


async def test_any_other_model_is_asked_for_its_most_likely_rewrite() -> None:
    """The other half, so the fix is not to stop asking: a model that takes a temperature is still
    asked at the redactor's own, which keeps one text redacted the same way twice."""
    sent: list[dict[str, Any]] = []
    engine = PipelineEngine(ProviderRegistry([_claude(sent, fixed_sampling=False)]))
    pipeline = Pipeline(steps=(PipelineStep(StepType.PII_FILTER, {"model": CLAUDE}),))

    await engine.run(pipeline, _request(), declaration_of=_claude_declared(fixed_sampling=False))

    assert [body.get("temperature") for body in sent] == [REDACTION_TEMPERATURE]
