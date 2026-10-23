"""Reranking, on both surfaces, under every control a model call has (`FRD-147`).

The two surfaces only parse: the predecessor's `POST /rerank` and Google's own ranking path. What
must hold is that no route reaches a reranker past a control — attribution, release, residency,
rate limit, budget — and that a ranking is booked in the unit Google bills. The upstream body is
pinned through the real Vertex adapter with only Google replaced.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.tokens import StaticTokenSource
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalRerankRequest,
    RankedRecord,
    RankedRecords,
    RerankRecord,
)
from aira_gateway.db.models import BudgetRead, ModelRead, RateLimitRead, RequestLog, UseCaseRead
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.vertex.adapters import VertexGeminiAdapter
from aira_gateway.upstreams.vertex.transport import VertexTransport

KIRA = "/kira/api/external/rerank"
NATIVE = "/v1/projects/their-project/locations/global/rankingConfigs/default_ranking_config:rank"
UC = {"x-aira-use-case": "uc"}
RANKER = "semantic-ranker-default-004"
RANKING_URL = (
    "https://discoveryengine.googleapis.com/v1/projects/p/locations/global"
    "/rankingConfigs/default_ranking_config:rank"
)

RECORDS = [
    {"id": "a", "title": "Kochen", "content": "Rezept für Nudeln"},
    {"id": "b", "title": "Wetter", "content": "Regen in Bonn morgen"},
]


def _app(**settings: Any):  # noqa: ANN202
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _ranker(app, model: str = "mock-1", **fields: Any) -> None:  # noqa: ANN001
    released = fields.pop("released", None)
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model=model,
                numeric_id=3001,
                capabilities=["rerank"],
                **{"approved": True, **fields},
            )
        )
        session.add(UseCaseRead(slug="uc", name="uc", allowed_models=released))
        await session.commit()


async def _rows(app) -> list[RequestLog]:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


def _kira(**over: Any) -> dict[str, Any]:
    return {"model_id": 3001, "query": "Wetter in Bonn", "records": RECORDS, **over}


def _native(**over: Any) -> dict[str, Any]:
    return {"model": "mock-1", "query": "Wetter in Bonn", "records": RECORDS, **over}


# == the predecessor's surface (FR-2 to FR-4) =====================================================


async def test_records_come_back_ordered_and_scored_in_the_predecessors_shape() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(KIRA, json=_kira(), headers=UC)

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [record["id"] for record in records] == ["b", "a"], "the better match first"
    assert set(records[0]) == {"id", "score", "title", "content"}
    assert records[0]["score"] > records[1]["score"]
    assert records[0]["content"] == "Regen in Bonn morgen"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (_kira(query=""), "an empty query"),
        (_kira(records=[]), "no records"),
        (_kira(records=[{**RECORDS[0], "id": ""}]), "a record without an id"),
        (_kira(records=[{**RECORDS[0], "title": ""}]), "a record without a title"),
        (_kira(records=[{**RECORDS[0], "content": ""}]), "a record without a content"),
        (_kira(records=[{"id": "a", "title": "t"}]), "a record missing a field"),
    ],
)
async def test_an_empty_field_is_the_predecessors_validation_error(
    body: dict[str, Any], why: str
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(KIRA, json=body, headers=UC)

    assert response.status_code == 422, why
    assert response.json()["code"] == "VALIDATION_ERROR", why


async def test_an_unknown_id_is_model_not_found() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(KIRA, json=_kira(model_id=3999), headers=UC)

    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_NOT_FOUND"


async def test_a_model_that_is_not_a_reranker_is_refused_by_its_own_code() -> None:
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", numeric_id=3001, capabilities=["generate"]))
            session.add(UseCaseRead(slug="uc", name="uc"))
            await session.commit()
        response = client.post(KIRA, json=_kira(), headers=UC)
        rows = await _rows(app)

    assert response.status_code == 422
    assert response.json()["code"] == "NO_RERANKER_CAPABILITIES"
    assert [(row.operation, row.outcome) for row in rows] == [("rerank", "invalid_request")]


async def test_a_reranker_answers_no_chat() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(
            "/kira/api/external/chat",
            json={"request": {"parts": [{"text": "hi"}]}, "model_id": 3001},
            headers=UC,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "NO_CHAT_CAPABILITIES"


async def test_models_lists_a_reranker_as_the_predecessor_does() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        listed = client.get("/kira/api/external/models").json()

    assert [(model["id"], model["capabilities"]) for model in listed] == [(3001, ["Reranker"])]


# == Google's ranking path (FR-5) =================================================================


async def test_googles_path_answers_in_googles_shape() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(NATIVE, json=_native(topN=1), headers=UC)

    assert response.status_code == 200, response.text
    (record,) = response.json()["records"]
    assert record["id"] == "b"
    assert set(record) == {"id", "score", "title", "content"}


async def test_ids_only_leaves_the_records_out() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(NATIVE, json=_native(ignoreRecordDetailsInResponse=True), headers=UC)

    assert response.status_code == 200, response.text
    assert all(set(record) == {"id", "score"} for record in response.json()["records"])


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        (NATIVE, {k: v for k, v in _native().items() if k != "model"}, 400),
        (NATIVE, _native(userLabels={"a": "b"}), 400),
        (NATIVE, _native(topN=0), 400),
        (NATIVE.replace(":rank", ":search"), _native(), 404),
    ],
    ids=["no-model", "a-field-not-modelled", "topN-zero", "another-method"],
)
async def test_googles_path_refuses_what_it_cannot_honour(
    path: str, body: dict[str, Any], status: int
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(path, json=body, headers=UC)

    assert response.status_code == status, response.text
    assert "error" in response.json(), "Google's envelope"


async def test_googles_path_refuses_a_model_that_does_not_rank() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-2", capabilities=["generate"]))
            await session.commit()
        response = client.post(NATIVE, json=_native(model="mock-2"), headers=UC)

    assert response.status_code == 400
    assert "reranker" in response.json()["error"]["message"]


# == every control (FR-6) =========================================================================


async def test_a_reranker_not_released_to_the_use_case_is_refused() -> None:
    google = Google()
    app = _google_app(google)
    with TestClient(app) as client:
        await _google_ranker(app, released=["something-else"])
        kira = client.post(KIRA, json=_kira(), headers=UC)
        native = client.post(NATIVE, json=_native(model=RANKER), headers=UC)

    assert kira.status_code == 400, kira.text
    assert native.status_code == 400, native.text
    assert RANKER in kira.json()["message"]
    assert google.seen == []


async def test_an_unapproved_reranker_is_refused() -> None:
    google = Google()
    app = _google_app(google)
    with TestClient(app) as client:
        await _google_ranker(app, approved=False)
        response = client.post(NATIVE, json=_native(model=RANKER), headers=UC)

    assert response.status_code != 200, response.text
    assert google.seen == []


async def test_a_ranking_weighs_one_request_per_hundred_records_against_a_rate_limit() -> None:
    """Google bills 250 records as three queries; a limit that counted one would let a ranking
    client spend three times its allowance."""
    many = [{"id": str(index), "title": "t", "content": "c"} for index in range(250)]
    app = _app(rerank_max_records=300)
    with TestClient(app) as client:
        await _ranker(app)
        async with app.state.db_sessionmaker() as session:
            session.add(
                RateLimitRead(id=1, use_case="uc", scope="use_case", limit_rpm=4, enabled=True)
            )
            await session.commit()
        first = client.post(KIRA, json=_kira(records=many), headers=UC)
        second = client.post(KIRA, json=_kira(records=many), headers=UC)

    assert first.status_code == 200, first.text
    # Three of four spent: a second ranking of three no longer fits, though it is one request.
    assert second.status_code == 429, second.text


async def test_a_ranking_is_booked_in_the_providers_unit_and_priced() -> None:
    many = [{"id": str(index), "title": "t", "content": "c"} for index in range(250)]
    app = _app(rerank_max_records=300)
    with TestClient(app) as client:
        # $1 per thousand queries is $1000 per million: the input price, per million units.
        await _ranker(
            app,
            input_price_per_million_nanos=1_000 * 10**9,
            output_price_per_million_nanos=0,
        )
        response = client.post(KIRA, json=_kira(records=many), headers=UC)
        (row,) = await _rows(app)

    assert response.status_code == 200, response.text
    assert (row.operation, row.prompt_tokens, row.completion_tokens) == ("rerank", 3, 0)
    assert row.cost_nanos == 3_000_000
    # What the row keeps of the answer: a description, never the records.
    assert row.response_payload is None or "Regen" not in json.dumps(row.response_payload)


async def test_a_ranking_weighs_one_per_text_where_the_pipeline_reads_each() -> None:
    """A redactor runs once per text — the query, then every record's title and content — and each
    run is a model call. An embedding batch weighs one per text for the same reason; a ranking
    weighed one per hundred records, so 200 records were 401 calls at a weight of two."""
    from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
    from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
    from aira_gateway.pipeline.engine import PipelineEngine

    class _Store:
        def __init__(self, *steps: PipelineStep) -> None:
            self._pipeline = Pipeline(steps=steps)

        async def get(self, use_case: str | None) -> Pipeline:
            return self._pipeline

    class _Redactor(MockProvider):
        async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
            return CanonicalResponse(
                model=request.model,
                text=request.messages[-1].text,
                usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            )

    weighed: dict[str, list[int]] = {}
    for name, steps in (
        ("redacted", (PipelineStep(type=StepType.PII_FILTER, config={"model": "mock-1"}),)),
        ("plain", ()),
    ):
        app = _app()
        registry = ProviderRegistry([_Redactor()])
        app.state.providers = registry
        app.state.pipeline_engine = PipelineEngine(registry)
        app.state.pipeline_store = _Store(*steps)
        with TestClient(app) as client:
            await _ranker(app)
            limits = app.state.rate_limits
            seen: list[int] = []

            async def spy(
                use_case: Any,
                caller: Any,
                units: int = 1,
                *,
                _check: Any = limits.check,
                _seen: list[int] = seen,
                **kwargs: Any,
            ) -> Any:
                _seen.append(units)
                return await _check(use_case, caller, units, **kwargs)

            limits.check = spy
            response = client.post(KIRA, json=_kira(), headers=UC)
        assert response.status_code == 200, response.text
        weighed[name] = seen

    assert weighed["redacted"] == [1 + 2 * len(RECORDS)], "the query and both fields of each"
    assert weighed["plain"] == [1], "without a step that reads each text, the provider's unit"


async def test_a_ranking_reserves_its_units_at_the_rate_they_are_booked_at() -> None:
    """A ranking is booked as input — its billing units at the input rate — so it is reserved
    there. Reserved at the output rate, a ranker priced on input alone, as Google's is, set aside
    nothing against a cost limit, and every ranking in flight passed a limit with room for one."""
    app = _app()
    with TestClient(app) as client:
        await _ranker(
            app,
            input_price_per_million_nanos=1_000 * 10**9,
            output_price_per_million_nanos=0,
        )
        reserved: list[Any] = []
        budgets = app.state.budgets
        guard = budgets.guard

        async def recording(*args: Any, estimated: Any = None, **kwargs: Any) -> Any:
            reserved.append(estimated)
            return await guard(*args, estimated=estimated, **kwargs)

        budgets.guard = recording
        response = client.post(KIRA, json=_kira(), headers=UC)

    assert response.status_code == 200, response.text
    assert reserved[0].tokens == 1, "one ranking unit, held as input"
    assert reserved[0].cost_nanos == 1_000_000, "one unit at the input rate, as it is booked"


async def test_an_unpriced_reranker_is_refused_where_spend_is_capped() -> None:
    google = Google()
    app = _google_app(google)
    with TestClient(app) as client:
        await _google_ranker(app)
        async with app.state.db_sessionmaker() as session:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    period="month",
                    limit_cost_nanos=10**9,
                    enabled=True,
                )
            )
            await session.commit()
        response = client.post(KIRA, json=_kira(), headers=UC)

    assert response.status_code == 400, response.text
    assert "price" in response.json()["message"]
    assert google.seen == []


async def test_more_records_than_one_request_may_carry_are_refused_before_anything_is_spent() -> (
    None
):
    many = [{"id": str(index), "title": "t", "content": "c"} for index in range(201)]
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(KIRA, json=_kira(records=many), headers=UC)

    assert response.status_code == 400, response.text
    assert "200" in response.json()["message"]


# == Google's ranking API (FR-8, §5.2) ============================================================


class Google:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "no"}})
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "records": [
                    {"id": record["id"], "score": 0.5, "title": record.get("title", "")}
                    for record in reversed(body["records"])
                ]
            },
        )


def _vertex(google: Google, **transport: Any) -> VertexGeminiAdapter:
    return VertexGeminiAdapter(
        VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(google)),
            **transport,
        ),
        [],
    )


def _request(**over: Any) -> CanonicalRerankRequest:
    fields: dict[str, Any] = {
        "model": RANKER,
        "addressing": {"regions": ["global"]},
        "query": "q",
        "records": [
            RerankRecord(id="a", title="t", content="c"),
            RerankRecord(id="b", content="d"),
        ],
    }
    return CanonicalRerankRequest(**{**fields, **over})


def _google_app(google: Google, **settings: Any):  # noqa: ANN202
    """The real Vertex adapter behind the app, permitted `global` for the reranker by name."""
    app = _app(allowed_model_regions=f"global={RANKER}", **settings)
    app.state.providers = ProviderRegistry(
        [_vertex(google, model_regions={RANKER: frozenset({"global"})})]
    )
    return app


async def _google_ranker(app, **fields: Any) -> None:  # noqa: ANN001
    await _ranker(
        app,
        model=RANKER,
        provider="vertex",
        publisher="google",
        addressing={"regions": ["global"]},
        **fields,
    )


async def test_the_body_google_receives() -> None:
    google = Google()
    adapter = _vertex(google, model_regions={RANKER: frozenset({"global"})})

    ranked = await adapter.rerank(_request())

    (sent,) = google.seen
    assert str(sent.url) == RANKING_URL
    assert sent.headers["x-goog-user-project"] == "p"
    assert sent.headers["authorization"] == "Bearer t"
    assert json.loads(sent.content) == {
        "model": RANKER,
        "query": "q",
        "records": [{"id": "a", "title": "t", "content": "c"}, {"id": "b", "content": "d"}],
    }
    assert [record.id for record in ranked] == ["b", "a"], "Google's order, not ours"
    assert ranked.served_region == "global"


async def test_global_is_refused_unless_permitted_for_the_reranker_by_name() -> None:
    google = Google()

    with pytest.raises(RegionNotAllowed):
        await _vertex(google).rerank(_request())
    assert google.seen == [], "the records never left"


async def test_a_location_in_the_eu_has_its_own_host() -> None:
    google = Google()
    await _vertex(google, allowed_regions=("eu",)).rerank(_request(addressing={"regions": ["eu"]}))

    assert str(google.seen[0].url).startswith("https://eu-discoveryengine.googleapis.com/")


async def test_an_alias_sends_the_model_it_names() -> None:
    google = Google()
    await _vertex(google, model_regions={"ranker": frozenset({"global"})}).rerank(
        _request(model="ranker", addressing={"regions": ["global"], "model": RANKER})
    )

    assert json.loads(google.seen[0].content)["model"] == RANKER


@pytest.mark.parametrize(("status", "kira"), [(503, 502), (400, 502), (429, 429)])
async def test_a_provider_failure_reaches_the_predecessors_client_as_it_expects(
    status: int, kira: int
) -> None:
    app = _google_app(Google(status))
    with TestClient(app) as client:
        await _google_ranker(app, input_price_per_million_nanos=1, output_price_per_million_nanos=0)
        response = client.post(KIRA, json=_kira(), headers=UC)

    assert response.status_code == kira, response.text


async def test_a_record_google_never_received_is_the_providers_fault() -> None:
    """A caller would look up a record it never had."""

    def stranger(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"records": [{"id": "z", "score": 0.9}]})

    adapter = VertexGeminiAdapter(
        VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(stranger)),
            model_regions={RANKER: frozenset({"global"})},
        ),
        [],
    )

    from aira_gateway.upstreams.base import UpstreamError

    with pytest.raises(UpstreamError) as caught:
        await adapter.rerank(_request())
    assert caught.value.status_code == 502


async def test_nothing_is_held_back_for_an_answer_a_ranking_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ranking holds back its unit and nothing else: the default output estimate for an answer
    it never writes would hold a thousand tokens against every budget for a one-unit request."""
    from aira_gateway.api.serving import controls

    held: list[Any] = []
    real = controls.estimate

    async def watched(*args: Any, **kwargs: Any) -> Any:
        held.append(await real(*args, **kwargs))
        return held[-1]

    monkeypatch.setattr(controls, "estimate", watched)
    app = _app(budget_estimate_output_tokens=1024)
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(KIRA, json=_kira(), headers=UC)

    assert response.status_code == 200, response.text
    assert [amounts.tokens for amounts in held] == [1]


async def test_googles_path_drops_record_details_a_provider_returned_anyway() -> None:
    """Asked for ids only, the caller gets ids only — whatever the provider sent back."""

    class Talkative(MockProvider):
        async def rerank(self, request: CanonicalRerankRequest) -> RankedRecords:
            return RankedRecords(
                [
                    RankedRecord(id=r.id, score=0.5, title=r.title, content=r.content)
                    for r in request.records
                ]
            )

    app = _app()
    app.state.providers = ProviderRegistry([Talkative("mock-1")])
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(NATIVE, json=_native(ignoreRecordDetailsInResponse=True), headers=UC)

    assert response.status_code == 200, response.text
    assert all(set(record) == {"id", "score"} for record in response.json()["records"])
