"""A pipeline's own model calls are recorded and billed (FRD-125).

Found by counting: one caller request with an LLM injection filter makes **two** model calls and
left **one** audit row. The classifier's tokens were invisible three ways at once —

    `FRD-601` reported a spend the call was not part of,
    `FRD-403`'s "unpriced traffic is counted apart, never as zero" was broken by counting it as
        *nothing at all*, which is the one thing that rule exists to forbid,
    `ADR-0013`'s auditable model access had a model call in it that nothing recorded.

All three follow from one omission, which is why one record closes all three.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "what is 2+2?"}]}]}


class _Guard:
    """A model that answers the classifier and the caller, counting what each cost."""

    def __init__(self, verdict: str = "SAFE") -> None:
        self._verdict = verdict
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("guard", "guard", ("generateContent",), provider="test", region="eu")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        classifying = request.max_output_tokens is not None and request.max_output_tokens <= 32
        return CanonicalResponse(
            model="guard",
            text=self._verdict if classifying else "4",
            usage=CanonicalUsage(
                prompt_tokens=30 if classifying else 5, completion_tokens=2 if classifying else 1
            ),
        )

    async def stream_generate(self, request: CanonicalRequest):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Store:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: Any) -> Pipeline:
        return self._pipeline


def _filter(**config: object) -> Pipeline:
    return Pipeline(
        steps=(
            PipelineStep(
                type=StepType.INJECTION_FILTER,
                config={"mode": "llm", "model": "guard", **config},
            ),
        ),
        fallback_models=(),
    )


def _app(pipeline: Pipeline, guard: _Guard):  # noqa: ANN201
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    registry = ProviderRegistry([guard])
    app.state.providers = registry
    # The engine holds its own reference from app construction — replacing only  leaves
    # it resolving against the original registry, which is how this test first ran the *heuristic*
    # classifier while claiming to test the LLM one.
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store(pipeline)
    return app


async def _rows(app) -> list[RequestLog]:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


async def test_a_pipeline_model_call_leaves_its_own_audit_row() -> None:
    """Two model calls, two rows. It used to be two calls and one row."""
    guard = _Guard()
    app = _app(_filter(action="flag"), guard)

    with TestClient(app) as client:
        assert client.post("/v1beta/models/guard:generateContent", json=_BODY).status_code == 200
        rows = await _rows(app)

    assert guard.calls == 2
    operations = sorted(row.operation for row in rows)
    assert operations == ["generateContent", "pipeline:injection_filter"]


async def test_the_pipeline_row_is_named_for_the_step_that_made_it() -> None:
    """So the reporting breakdown separates "what the use case asked" from "what governing it
    cost", instead of blending them into one figure nobody can take apart afterwards."""
    app = _app(_filter(action="flag"), _Guard())

    with TestClient(app) as client:
        client.post("/v1beta/models/guard:generateContent", json=_BODY)
        rows = await _rows(app)

    side = next(row for row in rows if row.operation.startswith("pipeline:"))
    assert side.operation == "pipeline:injection_filter"
    assert side.model == "guard"
    assert side.total_tokens == 32


async def test_the_pipeline_call_never_stores_the_prompt_a_second_time() -> None:
    """The classifier is *sent* the caller's text. Storing it again under a second row would
    double every retention and redaction question this system has (`FRD-404`, `FRD-406`)."""
    app = _app(_filter(action="flag"), _Guard())

    with TestClient(app) as client:
        client.post("/v1beta/models/guard:generateContent", json=_BODY)
        rows = await _rows(app)

    side = next(row for row in rows if row.operation.startswith("pipeline:"))
    assert side.request_payload is None
    assert side.response_payload is None


async def test_a_step_that_blocked_still_records_what_deciding_cost() -> None:
    """**The case that decides where this hook lives.** A filter that refused the request still
    spent the tokens it took to decide that, and a use case running a blocking filter over rejected
    traffic is paying for precisely those — so recording only on the served path would understate
    exactly the use cases that filter the most."""
    guard = _Guard(verdict="INJECTION")
    app = _app(_filter(action="block"), guard)

    with TestClient(app) as client:
        assert client.post("/v1beta/models/guard:generateContent", json=_BODY).status_code == 400
        rows = await _rows(app)

    operations = sorted(row.operation for row in rows)
    assert "pipeline:injection_filter" in operations


async def test_a_heuristic_filter_records_nothing_because_it_spent_nothing() -> None:
    """A regex costs nobody anything. A row per pattern match would be noise in the one table
    whose value is that everything in it happened."""
    guard = _Guard()
    app = _app(
        Pipeline(
            steps=(
                PipelineStep(type=StepType.INJECTION_FILTER, config={"mode": "heuristic"}),
            ),
            fallback_models=(),
        ),
        guard,
    )

    with TestClient(app) as client:
        client.post("/v1beta/models/guard:generateContent", json=_BODY)
        rows = await _rows(app)

    assert guard.calls == 1
    assert [row.operation for row in rows] == ["generateContent"]


async def test_the_pipeline_call_is_priced_like_any_other() -> None:
    """`FRD-403`'s rule is that unpriced traffic is counted *apart*, never as zero. A call counted
    as nothing at all is the failure that rule exists to forbid, arriving by a door it did not
    anticipate."""
    guard = _Guard()
    app = _app(_filter(action="flag"), guard)

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="guard",
                    input_price_per_million_nanos=1_000_000_000,
                    output_price_per_million_nanos=2_000_000_000,
                )
            )
            await session.commit()
        client.post("/v1beta/models/guard:generateContent", json=_BODY)
        rows = await _rows(app)

    side = next(row for row in rows if row.operation.startswith("pipeline:"))
    assert side.cost_nanos is not None and side.cost_nanos > 0


async def test_the_pipeline_call_is_booked_against_the_budget() -> None:
    """A mutation found this missing: every test above asserted the *audit* row, and the app under
    test had no budget configured, so booking zero tokens changed nothing anybody checked.

    An unbudgeted classifier is not a rounding error — measured against a real model it costs about
    as much as the answer it guards, so a use case at its limit would keep spending past it.
    """
    from aira_gateway.db.models import BudgetRead, BudgetUsage

    guard = _Guard()
    app = _app(_filter(action="flag"), guard)

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="demo",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_tokens=1_000_000,
                    enabled=True,
                )
            )
            await session.commit()

        client.post(
            "/v1beta/models/guard:generateContent", json=_BODY, headers={"x-aira-use-case": "demo"}
        )

        async with app.state.db_sessionmaker() as session:
            usage = list((await session.execute(select(BudgetUsage))).scalars())

    assert usage, "nothing was booked at all"
    # 32 for the classifier call, 6 for the answer.
    assert usage[0].tokens == 38


async def test_the_pipeline_call_is_not_counted_as_a_second_request() -> None:
    """The caller made **one** request. Counting the classifier as a second would inflate every
    request figure in reporting and could trip a *request* limit for traffic nobody sent."""
    from aira_gateway.db.models import BudgetRead, BudgetUsage

    app = _app(_filter(action="flag"), _Guard())

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="demo",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_requests=1000,
                    enabled=True,
                )
            )
            await session.commit()

        client.post(
            "/v1beta/models/guard:generateContent", json=_BODY, headers={"x-aira-use-case": "demo"}
        )

        async with app.state.db_sessionmaker() as session:
            usage = list((await session.execute(select(BudgetUsage))).scalars())

    assert usage[0].requests == 1
