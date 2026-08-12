"""A streamed request meets the same conditions as one that is not (`ADR-0012` §3).

Measured against the hermetic app on 2026-08-11, three requests that `:generateContent` refuses
by name and `:streamGenerateContent` **served with a 200**:

    a model no Global Administrator approved (`FRD-307`)      → 200, served
    a model the use case was never released (`FRD-308`)       → 200, served
    a `model_route` step re-targeting across providers        → 200, answered by the wrong server

The cause is one line: the streaming branch called `provider.stream_generate(...)` directly, while
the non-streaming branch went through `dispatch_with_fallback`, which is where the conditions are
asked. Residency (`FRD-115`), media types (`FRD-110`), tools, thinking and schemas all travel
through that same mechanism, so every one of them was bypassed with it — on the verb every chat
client and every coding assistant uses.

The `:embedContent` bypass, one verb over, and the same lesson: **a control belongs on the path
every branch takes, not inside one of them.**

The third case is the one that has evidence in it rather than authorisation. After routing, the
adapter was still the one that serves the model the *caller* named, so the answer came from server
A and was recorded, priced and audited as having come from server B — which is exactly the claim
`FRD-115` exists to make checkable.

What is deliberately **not** asserted here is a fallback. A stream still has none: once the first
chunk is on the wire the status is 200 and the answer has begun. The change is that a candidate
which does not qualify is refused *before* any of that, with a status the caller can read.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}


class _Server:
    """A real-looking adapter that says **which machine** answered.

    Not a test double: the exemption `FRD-307` grants those would make every case here pass
    without the rule existing at all.
    """

    def __init__(self, model: str, machine: str = "") -> None:
        self._model = model
        self._machine = machine or model

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                self._model,
                self._model,
                ("generateContent", "streamGenerateContent"),
                "acme",
                "acme",
                "",
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=request.model,
            text=f"served-by:{self._machine}",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request: CanonicalRequest):  # noqa: ANN201
        yield CanonicalChunk(
            text_delta=f"served-by:{self._machine}",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            finish_reason="stop",
        )

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Router(_Server):
    """A classifier that always answers with the same category."""

    def __init__(self, model: str, verdict: str) -> None:
        super().__init__(model)
        self._verdict = verdict

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=self._model,
            text=self._verdict,
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(pipeline: Pipeline, *providers: object) -> Any:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(pipeline)
    return app


async def _catalogue(app: Any, *models: tuple[str, bool], capability: str = "generate") -> None:
    async with app.state.db_sessionmaker() as session:
        for model, approved in models:
            session.add(ModelRead(model=model, approved=approved, capabilities=[capability]))
        await session.commit()


async def _release(app: Any, slug: str, models: list[str] | None) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, allowed_models=models))
        await session.commit()


def _stream(client: TestClient, model: str, slug: str | None = None) -> Any:
    headers = {"X-AIRA-Use-Case": slug} if slug else {}
    return client.post(f"/v1beta/models/{model}:streamGenerateContent", json=_BODY, headers=headers)


# == the three holes, in the order they were measured =============================================


async def test_an_unapproved_model_cannot_be_reached_by_streaming() -> None:
    """`FRD-307`: only a model a Global Administrator catalogued **and approved** may be used.
    Served with a 200 over this verb until 2026-08-11."""
    app = _app(Pipeline(), _Server("unapproved-1"))
    with TestClient(app) as client:
        await _catalogue(app, ("unapproved-1", False))
        response = _stream(client, "unapproved-1")

    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    # The two refusals stay apart: "not in the catalog" is a different action from "not approved".
    assert "approved" in message


async def test_a_withheld_model_cannot_be_reached_by_streaming() -> None:
    """`FRD-308`: a use case reaches the models somebody released for it. The non-streaming verb
    refused this by name while this one answered."""
    app = _app(Pipeline(), _Server("released-1"), _Server("withheld-1"))
    with TestClient(app) as client:
        await _catalogue(app, ("released-1", True), ("withheld-1", True))
        await _release(app, "uc", ["released-1"])
        response = _stream(client, "withheld-1", slug="uc")

    assert response.status_code == 400, response.text
    assert "withheld-1" in response.json()["error"]["message"]


async def test_a_streamed_request_is_answered_by_the_model_it_was_routed_to() -> None:
    """Provenance, not authorisation. The adapter was resolved from the model the *caller* named,
    before the pipeline ran — so a routed request was answered by one machine and recorded as
    having come from another."""
    pipeline = Pipeline(
        steps=(
            PipelineStep(
                StepType.MODEL_ROUTE,
                {"model": "router", "categories": [{"name": "cheap", "model": "on-server-b"}]},
            ),
        )
    )
    app = _app(
        pipeline,
        _Server("on-server-a", machine="A"),
        _Server("on-server-b", machine="B"),
        _Router("router", "cheap"),
    )
    with TestClient(app) as client:
        await _catalogue(app, ("on-server-a", True), ("on-server-b", True), ("router", True))
        response = _stream(client, "on-server-a")

    assert response.status_code == 200, response.text
    assert "served-by:B" in response.text
    assert "served-by:A" not in response.text


# == and the ordinary case still works ============================================================


async def test_a_released_and_approved_model_still_streams() -> None:
    """The point of the change is that a refusal happens where one is due — not that streaming
    became harder to reach."""
    app = _app(Pipeline(), _Server("released-1"))
    with TestClient(app) as client:
        await _catalogue(app, ("released-1", True))
        await _release(app, "uc", ["released-1"])
        response = _stream(client, "released-1", slug="uc")

    assert response.status_code == 200, response.text
    assert "served-by:released-1" in response.text


# == and the same hole, one verb over =============================================================
#
# `:embedContent` has no chain either — there is nothing to fall back *to*, because a vector from a
# second model is not a substitute for a vector from the first — so nobody wrote one, and the
# conditions went with it. Measured the same afternoon, the same two refusals, the same 200s. The
# literal `:embedContent` bypass of `FRD-405`, for the third time in this codebase's history; the
# third is what makes it a class, and `test_every_dispatch_applies_the_conditions.py` is the
# answer to the class.


def _embed(client: TestClient, model: str, slug: str | None = None) -> Any:
    headers = {"X-AIRA-Use-Case": slug} if slug else {}
    return client.post(
        f"/v1beta/models/{model}:embedContent",
        json={"content": {"parts": [{"text": "hello"}]}},
        headers=headers,
    )


class _Embedder(_Server):
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._model, self._model, ("embedContent",), "acme", "acme", "")]


async def test_an_unapproved_model_cannot_be_reached_by_embedding() -> None:
    app = _app(Pipeline(), _Embedder("emb-unapproved"))
    with TestClient(app) as client:
        await _catalogue(app, ("emb-unapproved", False), capability="embed")
        response = _embed(client, "emb-unapproved")

    assert response.status_code == 400, response.text
    # The **sentence**, not the model name: `emb-unapproved` also appears in "does not support
    # embedding", so a test matching only the name would pass with the rule removed and the
    # catalogue mis-declared. That is how the first draft of this case passed for the wrong
    # reason — the same trap `FRD-116` recorded about an assertion matching both messages.
    assert "has not been approved" in response.json()["error"]["message"]


async def test_a_withheld_model_cannot_be_reached_by_embedding() -> None:
    app = _app(Pipeline(), _Embedder("emb-1"))
    with TestClient(app) as client:
        await _catalogue(app, ("emb-1", True), capability="embed")
        await _release(app, "uc", [])
        response = _embed(client, "emb-1", slug="uc")

    assert response.status_code == 400, response.text
    assert "no model released" in response.json()["error"]["message"]


async def test_a_released_and_approved_model_still_embeds() -> None:
    app = _app(Pipeline(), _Embedder("emb-1"))
    with TestClient(app) as client:
        await _catalogue(app, ("emb-1", True), capability="embed")
        await _release(app, "uc", ["emb-1"])
        response = _embed(client, "emb-1", slug="uc")

    assert response.status_code == 200, response.text
    assert response.json()["embedding"]["values"] == [0.0]
