"""A use case's fallback chain for each kind of request (`FRD-310`, `ADR-0052`).

Three kinds — text generation, embedding, ranking — each with a chain the use case keeps in its
pipeline, empty until an administrator fills it: a model that cannot answer is then the caller's
error. A chain serves its own kind only. Every candidate meets every condition at every hop, the use
case's release among them, and a candidate of another kind is passed over **by name**: an embedding
model is never asked for an answer, nor a model that writes text for a vector. The installation
keeps no chain of its own: what the release before this one wrote is read by nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import select

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
from aira_gateway.db.models import (
    FallbackChainRead,
    ModelRead,
    PipelineConfigRead,
    RequestLog,
    UseCaseRead,
)
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType, problem_in
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.store import PipelineStore
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
    app: Any, declared: dict[str, dict[str, Any]], released: list[str] | None = None
) -> None:
    async with app.state.db_sessionmaker() as session:
        for number, (name, declaration) in enumerate(declared.items()):
            session.add(
                ModelRead(model=name, numeric_id=8100 + number, approved=True, **declaration)
            )
        session.add(UseCaseRead(slug=SLUG, name=SLUG, allowed_models=released))
        await session.commit()


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


def _skipped(row: RequestLog) -> dict[str, str]:
    return {d["to"]: d["why"] for d in row.pipeline_decisions or [] if d.get("action") == "skipped"}


# == text generation ==============================================================================


@pytest.mark.parametrize("verb", ["generateContent", "streamGenerateContent"])
async def test_a_failing_model_is_replaced_along_the_use_cases_text_chain(verb: str) -> None:
    """What the owner asked for: set in the use case's pipeline, tried in order, on both verbs —
    and the row says which candidate answered."""
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(fallback_models=("backup-1",)))
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS})
        response = client.post(f"/v1beta/models/primary-1:{verb}", json=_TEXT, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "by:backup-1" in response.text
    assert [(row.model, row.model_selection) for row in rows] == [("backup-1", "fallback:1")]


@pytest.mark.parametrize(
    "pipeline",
    [
        None,
        Pipeline(steps=(PipelineStep(StepType.INJECTION_FILTER, {"mode": "heuristic"}),)),
        Pipeline(embedding_fallback_models=("backup-1",), ranking_fallback_models=("backup-1",)),
    ],
    ids=["no-pipeline", "steps-only", "other-kinds-only"],
)
async def test_without_a_text_chain_a_failure_is_the_callers_error(
    pipeline: Pipeline | None,
) -> None:
    """The default: no model is stored, so the error goes straight to the caller and nothing else
    is called — whatever else the pipeline holds, a chain for another kind included."""
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, pipeline)
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS})
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 503
    assert models.called == ["primary-1"]


async def test_the_installation_chain_the_previous_release_wrote_is_read_by_nothing() -> None:
    """The table stays one release for the instances still serving it during a rolling update;
    this one asks it nothing."""
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models)
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS})
        async with app.state.db_sessionmaker() as session:
            session.add(FallbackChainRead(kind="generate", models=["backup-1"]))
            await session.commit()
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 503
    assert models.called == ["primary-1"]


async def test_a_chain_reaches_only_what_the_use_case_was_released() -> None:
    """The release holds at every hop: a model this use case may not call is passed over by name."""
    models = _Governed("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(fallback_models=("backup-1",)))
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS}, released=["primary-1"])
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )
        rows = await _rows(app)

    assert response.status_code == 503
    assert models.called == ["primary-1"]
    assert "released" in _skipped(rows[0])["backup-1"]


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
    skipped = _skipped(rows[0])
    assert list(skipped) == ["emb-1"]
    assert "text generation" in skipped["emb-1"]


async def test_a_pipeline_with_steps_falls_back_along_its_text_chain() -> None:
    """The chain is read after the steps ran, from the same pipeline."""
    steps = (PipelineStep(StepType.INJECTION_FILTER, {"mode": "heuristic"}),)
    models = _Models("primary-1", "backup-1", failing=("primary-1",))
    app = _app(models, Pipeline(steps=steps, fallback_models=("backup-1",)))
    with TestClient(app) as client:
        await _setup(app, {"primary-1": GENERATORS, "backup-1": GENERATORS})
        response = client.post(
            "/v1beta/models/primary-1:generateContent", json=_TEXT, headers=HEADERS
        )

    assert response.status_code == 200, response.text
    assert models.called == ["primary-1", "backup-1"]


async def test_a_step_never_asks_a_model_that_cannot_write_text() -> None:
    """Every step asks its model for text. An embedding model named as a redactor is withheld by
    name, and the step degrades as it does for any model it may not call — to a block."""
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


async def test_an_embedding_falls_back_along_the_use_cases_embedding_chain() -> None:
    """Booked, recorded and named as the model that gave the vectors — priced at its price."""
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models, Pipeline(embedding_fallback_models=("emb-2",)))
    with TestClient(app) as client:
        await _setup(app, PRICED_EMBEDDERS)
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
    app = _app(models, Pipeline(embedding_fallback_models=("emb-2",)))
    with TestClient(app) as client:
        await _setup(app, PRICED_EMBEDDERS)
        response = client.post(
            "/kira/api/external/embed", json={"text": "hallo", "model_id": 8100}, headers=HEADERS
        )
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert response.json()["vector"] == [5.0, 5.0]
    assert [(row.model, row.model_selection, row.cost_nanos) for row in rows] == [
        ("emb-2", "fallback:1", 2_000_000)
    ]


@pytest.mark.parametrize(
    "pipeline",
    [None, Pipeline(fallback_models=("emb-2",), ranking_fallback_models=("emb-2",))],
    ids=["no-pipeline", "other-kinds-only"],
)
async def test_without_an_embedding_chain_an_embedding_failure_is_the_callers_error(
    pipeline: Pipeline | None,
) -> None:
    """A chain serves its own kind only: a model in the text chain is no stand-in for vectors."""
    models = _Models("emb-1", "emb-2", failing=("emb-1",))
    app = _app(models, pipeline)
    with TestClient(app) as client:
        await _setup(app, {"emb-1": EMBEDDERS, "emb-2": EMBEDDERS})
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)

    assert response.status_code == 503
    assert models.called == ["emb-1"]


async def test_an_embedding_never_falls_back_to_vectors_of_another_width_or_a_text_model() -> None:
    models = _Models("emb-1", "gen-1", "wide-1", failing=("emb-1",))
    app = _app(models, Pipeline(embedding_fallback_models=("gen-1", "wide-1")))
    with TestClient(app) as client:
        await _setup(app, {"emb-1": EMBEDDERS, "gen-1": GENERATORS, "wide-1": WIDER})
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
    app = _app(models, Pipeline(embedding_fallback_models=("emb-2",)))
    with TestClient(app) as client:
        await _setup(app, {"emb-1": {"capabilities": ["embed"]}, "emb-2": EMBEDDERS})
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED, headers=HEADERS)

    assert response.status_code == 503
    assert models.called == ["emb-1"]


# == ranking ======================================================================================

_RANK = "/v1/projects/p/locations/global/rankingConfigs/default_ranking_config:rank"
_RANK_BODY = {"model": "rank-1", "query": "q", "records": [{"id": "a", "content": "x"}]}
_RANKERS_PRICED = {
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
}


async def test_a_ranking_falls_back_to_a_reranker_and_passes_over_anything_else() -> None:
    models = _Models("rank-1", "gen-1", "rank-2", failing=("rank-1",))
    app = _app(models, Pipeline(ranking_fallback_models=("gen-1", "rank-2")))
    with TestClient(app) as client:
        await _setup(app, _RANKERS_PRICED)
        response = client.post(_RANK, json=_RANK_BODY, headers=HEADERS)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert models.called == ["rank-1", "rank-2"]
    # One ranking unit at rank-2's price per million units, as `FRD-147` FR-7 books it.
    assert [(row.model, row.model_selection, row.cost_nanos) for row in rows] == [
        ("rank-2", "fallback:2", 2 * 10**9)
    ]


async def test_without_a_ranking_chain_a_ranking_failure_is_the_callers_error() -> None:
    models = _Models("rank-1", "rank-2", failing=("rank-1",))
    app = _app(models, Pipeline(fallback_models=("rank-2",)))
    with TestClient(app) as client:
        await _setup(app, _RANKERS_PRICED)
        response = client.post(_RANK, json=_RANK_BODY, headers=HEADERS)

    assert response.status_code >= 500
    assert models.called == ["rank-1"]


# == what the read-model holds ====================================================================


async def test_the_consumer_applies_every_kinds_chain_and_reads_an_absent_one_as_none() -> None:
    """The whole event replaces what an earlier one said: a chain it leaves out is none, and the
    mode the previous release reads is written empty — a pipeline from before modes existed."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(PipelineConfigRead(use_case=SLUG, fallback_mode="default"))
            await session.commit()
            await apply_event(
                session,
                "pipeline.upserted",
                {
                    "use_case": SLUG,
                    "steps": [],
                    "fallback_models": ["gen-2"],
                    "embedding_fallback_models": ["emb-2"],
                    "ranking_fallback_models": ["rank-2"],
                },
            )
        whole = await PipelineStore(app.state.db_sessionmaker).get(SLUG)
        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session,
                "pipeline.upserted",
                {"use_case": SLUG, "steps": [], "fallback_models": ["gen-2"]},
            )
            mode = (await session.get(PipelineConfigRead, SLUG)).fallback_mode
        older = await PipelineStore(app.state.db_sessionmaker).get(SLUG)

    assert whole == Pipeline(
        fallback_models=("gen-2",),
        embedding_fallback_models=("emb-2",),
        ranking_fallback_models=("rank-2",),
    )
    assert older == Pipeline(fallback_models=("gen-2",))
    assert mode is None


async def test_a_pipeline_with_only_an_embedding_chain_is_not_empty() -> None:
    """The store answers no pipeline for one that changes nothing — and a chain for any kind is a
    change, or an embedding chain alone would never be read."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(
                PipelineConfigRead(use_case=SLUG, steps=[], embedding_fallback_models=["emb-2"])
            )
            session.add(PipelineConfigRead(use_case="plain", steps=[], fallback_models=[]))
            await session.commit()
        store = PipelineStore(app.state.db_sessionmaker)
        embedding_only, plain = await store.get(SLUG), await store.get("plain")

    assert embedding_only == Pipeline(embedding_fallback_models=("emb-2",))
    assert plain is None


async def test_the_installation_chain_event_is_not_applied_and_not_reported() -> None:
    """The compacted topic still holds the last one the previous release sent; a rebuild reads it,
    applies nothing, and does not report a control that did not take effect — there is none."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            with structlog.testing.capture_logs() as logs:
                await apply_event(
                    session, "fallback_chain.upserted", {"kind": "generate", "models": ["x"]}
                )
                await apply_event(session, "fallback_chain.renamed", {"kind": "generate"})
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(FallbackChainRead))).scalars())

    assert rows == []
    # The guard on the guard: an event nobody knows is still reported.
    assert [
        log.get("event_type") for log in logs if log["event"] == "config_event_not_applied"
    ] == ["fallback_chain.renamed"]


def test_every_kinds_chain_is_held_to_the_bounds() -> None:
    """Truncated rather than dropped, like the text chain always was: names only, sixteen of them,
    each a catalogue name's length."""
    names = [f"m-{n}" for n in range(30)]
    pipeline = Pipeline.from_dict(
        {
            "embedding_fallback_models": [*names, 7, "", None],
            "ranking_fallback_models": "rank-1",
        }
    )

    assert pipeline.embedding_fallback_models == tuple(names[:16])
    assert pipeline.ranking_fallback_models == ()


@pytest.mark.parametrize("field", ["embedding_fallback_models", "ranking_fallback_models"])
def test_a_dry_run_body_with_a_malformed_chain_is_refused_by_name(field: str) -> None:
    assert problem_in({"steps": [], field: "emb-1"}) == (
        f"pipeline.{field} must be a list of at most 16 model names."
    )
    assert problem_in({"steps": [], field: ["x" * 500]}) == (
        f"pipeline.{field} must hold model names of at most 128 characters."
    )
