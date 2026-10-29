"""The installation's fallback chain per kind of request, and what a use case does with it.

`FRD-310`.

Three kinds — text generation, embedding, ranking — each with a chain the installation sets, empty
until it does; and a use case that follows them, keeps its own for text generation, or has none.
Every candidate meets every condition at every hop, the use case's release among them, and a
candidate of another kind is passed over **by name**: an embedding model is never asked for an
answer, nor a model that writes text for a vector.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.fallback import FallbackMode
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalRerankRequest,
    CanonicalResponse,
    CanonicalUsage,
    EmbeddingVectors,
    RankedRecord,
    RankedRecords,
)
from aira_gateway.db.models import FallbackChainRead, ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

SLUG = "kinds"
HEADERS = {"X-AIRA-Use-Case": SLUG}
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)
_TEXT = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
_EMBED = {"content": {"parts": [{"text": "hallo"}]}}


class _Models:
    """Every model a case needs, on one adapter: each answers in its own kind, or fails."""

    is_test_double = True
    serves_provider = "kinds"

    def __init__(self, *names: str, failing: tuple[str, ...] = ()) -> None:
        self._names = names
        self._failing = failing
        self.called: list[str] = []

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent", "embedContent")
        return [UpstreamModel(name, name, verbs, "kinds") for name in self._names]

    def _attempt(self, model: str) -> None:
        self.called.append(model)
        if model in self._failing:
            raise UpstreamError(f"{model} is unavailable", 503)

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self._attempt(request.model)
        return CanonicalResponse(model=request.model, text=f"by:{request.model}", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        self._attempt(request.model)
        yield CanonicalChunk(text_delta=f"by:{request.model}", usage=_USAGE, finish_reason="stop")

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        self._attempt(request.model)
        width = request.dimensions or 2
        # A thousand tokens a text, reported as Vertex reports them, so a booking can be priced.
        return EmbeddingVectors(
            ([float(len(request.model))] * width for _ in request.texts),
            input_tokens=1_000 * len(request.texts),
        )

    async def rerank(self, request: CanonicalRerankRequest) -> RankedRecords:
        self._attempt(request.model)
        return RankedRecords([RankedRecord(id=record.id, score=0.5) for record in request.records])


class _Governed(_Models):
    """The same models, held to the release as a real adapter's are: a test double is exempt from
    approval and release (`FRD-307`, `FRD-308`), which is no test of either."""

    is_test_double = False


class _Fixed:
    def __init__(self, pipeline: Pipeline | None) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline | None:
        return self._pipeline


GENERATORS = {"capabilities": ["generate"]}
#: Two vectors wide, or three: a caller's index takes the width the model asked for gives.
EMBEDDERS = {"capabilities": ["embed"], "embedding": {"default": 2, "dimensions": [2]}}
#: The same two, priced apart, so a booking says which of them it was priced as.
PRICED_EMBEDDERS = {
    "emb-1": {
        **EMBEDDERS,
        "input_price_per_million_nanos": 10**9,
        "output_price_per_million_nanos": 10**9,
    },
    "emb-2": {
        **EMBEDDERS,
        "input_price_per_million_nanos": 2 * 10**9,
        "output_price_per_million_nanos": 2 * 10**9,
    },
}
WIDER = {"capabilities": ["embed"], "embedding": {"default": 3, "dimensions": [3]}}
RERANKERS = {"capabilities": ["rerank"]}


def _app(models: _Models, pipeline: Pipeline | None = None) -> Any:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([models])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Fixed(pipeline)
    return app


async def _setup(
    app: Any,
    declared: dict[str, dict[str, Any]],
    chains: dict[str, list[str]] | None = None,
    released: list[str] | None = None,
) -> None:
    async with app.state.db_sessionmaker() as session:
        for number, (name, declaration) in enumerate(declared.items()):
            session.add(
                ModelRead(model=name, numeric_id=8100 + number, approved=True, **declaration)
            )
        for kind, models in (chains or {}).items():
            session.add(FallbackChainRead(kind=kind, models=models))
        session.add(UseCaseRead(slug=SLUG, name=SLUG, allowed_models=released))
        await session.commit()


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


# == text generation ==============================================================================


@pytest.mark.parametrize("verb", ["generateContent", "streamGenerateContent"])
async def test_a_use_case_with_no_chain_of_its_own_follows_the_installations(verb: str) -> None:
    """The default the owner asked for, set: a model that cannot answer is replaced by the
    installation's chain for text generation, on both verbs, and the row says it was."""
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(
            app,
            {"primary-1": GENERATORS, "backup-1": GENERATORS},
            chains={"generate": ["backup-1"]},
        )
        response = client.post(f"/v1beta/models/primary-1:{verb}", json=_TEXT, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "by:backup-1" in response.text
    assert [(row.model, row.model_selection) for row in rows] == [("backup-1", "fallback:1")]


async def test_without_an_installation_chain_a_failure_is_the_callers_error() -> None:
    """The default as shipped: no chain, so the upstream's failure — nothing substituted."""
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS})
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 503
    assert models.called == ["primary-1"]


async def test_a_use_case_that_wants_none_is_answered_by_no_other_model() -> None:
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(fallback_mode=FallbackMode.NONE))
    with TestClient(app) as client:
        await _setup(
            app,
            {"primary-1": GENERATORS, "backup-1": GENERATORS},
            chains={"generate": ["backup-1"]},
        )
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 503
    assert models.called == ["primary-1"]


async def test_a_use_case_with_its_own_chain_uses_it_instead_of_the_installations() -> None:
    models = _Models("primary-1", "own-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(fallback_models=("own-1",), fallback_mode=FallbackMode.OWN))
    with TestClient(app) as client:
        await _setup(
            app,
            {"primary-1": GENERATORS, "own-1": GENERATORS, "backup-1": GENERATORS},
            chains={"generate": ["backup-1"]},
        )
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 200, response.text
    assert models.called == ["primary-1", "own-1"]


async def test_the_installations_chain_reaches_only_what_the_use_case_was_released() -> None:
    """The release holds at every hop: a chain set for the installation is not a release, and a
    model this use case may not call is passed over by name."""
    models = _Governed("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(
            app,
            {"primary-1": GENERATORS, "backup-1": GENERATORS},
            chains={"generate": ["backup-1"]},
            released=["primary-1"],
        )
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )
        rows = await _rows(app)

    assert response.status_code == 503
    assert models.called == ["primary-1"]
    skipped = [d for d in rows[0].pipeline_decisions or [] if d.get("action") == "skipped"]
    assert skipped and skipped[0]["to"] == "backup-1"
    assert "released" in skipped[0]["why"]


@pytest.mark.parametrize("verb", ["generateContent", "streamGenerateContent"])
async def test_a_model_of_another_kind_in_a_text_chain_is_passed_over_by_name(verb: str) -> None:
    """The owner's case: an embedding model in a generation chain was called for an answer it
    has none of, and its vendor's refusal read as an outage. Now it is never called."""
    models = _Models("primary-1", "emb-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(fallback_models=("emb-1", "backup-1")))
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "emb-1": EMBEDDERS, "backup-1": GENERATORS})
        response = client.post(f"/v1beta/models/primary-1:{verb}", json=_TEXT, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert models.called == ["primary-1", "backup-1"], "the embedding model is never called"
    skipped = [d for d in rows[0].pipeline_decisions or [] if d.get("action") == "skipped"]
    assert [(d["to"], "text generation" in d["why"]) for d in skipped] == [("emb-1", True)]


async def test_a_pipeline_with_steps_still_follows_the_installations_chain() -> None:
    """A use case's steps say nothing about its fallback: it follows the installation's chain
    until it keeps its own, whatever else its pipeline does."""
    from aira_gateway.pipeline.config import PipelineStep, StepType

    steps = (PipelineStep(StepType.INJECTION_FILTER, {"mode": "heuristic"}),)
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(steps=steps))
    with TestClient(app) as client:
        await _setup(
            app,
            {"primary-1": GENERATORS, "backup-1": GENERATORS},
            chains={"generate": ["backup-1"]},
        )
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 200, response.text
    assert models.called == ["primary-1", "backup-1"]


async def test_a_step_never_asks_a_model_that_cannot_write_text() -> None:
    """Every step asks its model for text. An embedding model named as a redactor is withheld by
    name, and the step degrades as it does for any model it may not call — to a block."""
    from aira_gateway.pipeline.config import PipelineStep, StepType

    steps = (PipelineStep(StepType.PII_FILTER, {"model": "emb-1"}),)
    models = _Models("primary-1", "emb-1")
    app = _app(models, Pipeline(steps=steps))
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "emb-1": EMBEDDERS})
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 400
    assert "emb-1" in response.text and "text generation" in response.text
    assert models.called == [], "neither the redactor nor the model was asked"


# == embedding ====================================================================================


async def test_an_embedding_falls_back_to_a_model_of_the_same_width() -> None:
    """Booked, recorded and named as the model that gave the vectors — priced at its price."""
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(app, PRICED_EMBEDDERS, chains={"embed": ["emb-2"]})
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert response.json()["embedding"]["values"] == [5.0, 5.0]
    assert response.headers["x-aira-model"] == "emb-2"
    # A thousand tokens at emb-2's two per million: 0.002, not emb-1's 0.001.
    assert [(row.model, row.model_selection, row.cost_nanos) for row in rows] == [
        ("emb-2", "fallback:1", 2_000_000)
    ]


async def test_the_kira_surface_embeds_along_the_same_chain() -> None:
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(app, PRICED_EMBEDDERS, chains={"embed": ["emb-2"]})
        response = client.post(
            "/kira/api/external/embed", json={"text": "hallo", "model_id": 8100}, headers=HEADERS
        )
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert response.json()["vector"] == [5.0, 5.0]
    assert [(row.model, row.model_selection, row.cost_nanos) for row in rows] == [
        ("emb-2", "fallback:1", 2_000_000)
    ]


async def test_an_embedding_never_falls_back_to_vectors_of_another_width_or_a_text_model() -> None:
    models = _Models("emb-1", "gen-1", "wide-1", failing=("emb-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(
            app,
            {"emb-1": EMBEDDERS, "gen-1": GENERATORS, "wide-1": WIDER},
            chains={"embed": ["gen-1", "wide-1"]},
        )
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 503
    assert models.called == ["emb-1"]
    reasons = {d["to"]: d["why"] for d in rows[0].pipeline_decisions or [] if d.get("to")}
    assert "no embedding" in reasons["gen-1"]
    assert "[3]" in reasons["wide-1"], reasons["wide-1"]


async def test_an_embedding_whose_width_is_undeclared_has_no_fallback() -> None:
    """No width to hold a stand-in to, so none can be shown to fit the caller's index."""
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(
            app,
            {"emb-1": {"capabilities": ["embed"]}, "emb-2": EMBEDDERS},
            chains={"embed": ["emb-2"]},
        )
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)

    assert response.status_code == 503
    assert models.called == ["emb-1"]


async def test_a_use_case_that_wants_none_keeps_its_embeddings_to_the_model_it_named() -> None:
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models, Pipeline(fallback_mode=FallbackMode.NONE))
    with TestClient(app) as client:
        await _setup(app, {"emb-1": EMBEDDERS, "emb-2": EMBEDDERS}, chains={"embed": ["emb-2"]})
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)

    assert response.status_code == 503
    assert models.called == ["emb-1"]


async def test_a_use_cases_own_text_chain_leaves_its_embeddings_to_the_installations() -> None:
    """`own` is about text generation; the other kinds follow the installation's chains."""
    models = _Models("emb-1", "emb-2", "gen-1", failing=("emb-1",))
    app = _app(models, Pipeline(fallback_models=("gen-1",), fallback_mode=FallbackMode.OWN))
    with TestClient(app) as client:
        await _setup(
            app,
            {"emb-1": EMBEDDERS, "emb-2": EMBEDDERS, "gen-1": GENERATORS},
            chains={"embed": ["emb-2"]},
        )
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert models.called == ["emb-1", "emb-2"]


# == ranking ======================================================================================


async def test_a_ranking_falls_back_to_a_reranker_and_passes_over_anything_else() -> None:
    models = _Models("rank-1", "gen-1", "rank-2", failing=("rank-1",))
    app = _app(models)
    body = {"model": "rank-1", "query": "q", "records": [{"id": "a", "content": "x"}]}
    with TestClient(app) as client:
        await _setup(
            app,
            {
                "rank-1": {
                    **RERANKERS,
                    "input_price_per_million_nanos": 10**15,
                    "output_price_per_million_nanos": 0,
                },
                "gen-1": GENERATORS,
                "rank-2": {
                    **RERANKERS,
                    "input_price_per_million_nanos": 2 * 10**15,
                    "output_price_per_million_nanos": 0,
                },
            },
            chains={"rerank": ["gen-1", "rank-2"]},
        )
        response = client.post(
            "/v1/projects/p/locations/global/rankingConfigs/default_ranking_config:rank",
            json=body,
            headers=HEADERS,
        )
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert models.called == ["rank-1", "rank-2"]
    # One ranking unit at rank-2's price per million units, as `FRD-147` FR-7 books it.
    assert [(row.model, row.model_selection, row.cost_nanos) for row in rows] == [
        ("rank-2", "fallback:2", 2 * 10**9)
    ]


# == what the consumer applies ====================================================================


async def test_the_installations_chain_arrives_bounded_and_whole() -> None:
    """Held to the bounds a use case's own chain has: a direct write to the topic is a write to
    every use case that follows the chain. A kind this build does not know is left alone."""
    names = [f"m-{n}" for n in range(30)]
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session, "fallback_chain.upserted", {"kind": "embed", "models": [*names, 7, ""]}
            )
            await apply_event(
                session, "fallback_chain.upserted", {"kind": "vision", "models": ["x"]}
            )
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(FallbackChainRead))).scalars())

    assert [(row.kind, row.models) for row in rows] == [("embed", names[:16])]


@pytest.mark.parametrize(
    ("stored", "chain", "expected"),
    [
        (None, [], FallbackMode.DEFAULT),
        (None, ["own-1"], FallbackMode.OWN),
        ("none", ["own-1"], FallbackMode.NONE),
        ("Default", [], FallbackMode.NONE),
    ],
)
def test_a_stored_mode_is_read_as_what_it_said(
    stored: str | None, chain: list[str], expected: FallbackMode
) -> None:
    """Absent from an older Management: what such a pipeline always meant. A word this build does
    not know: `none`, which substitutes no model."""
    pipeline = Pipeline.from_dict({"steps": [], "fallback_models": chain, "fallback_mode": stored})

    assert pipeline.mode is expected
