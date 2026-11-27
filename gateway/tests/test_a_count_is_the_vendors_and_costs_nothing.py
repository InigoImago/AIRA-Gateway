"""`:countTokens` answers with the vendor's own count, and costs nothing (`FRD-155`).

Every Gemini SDK offers it, LangChain calls it from `get_num_tokens`, and the Gemini CLI uses it to
decide when to compress a conversation. Eleven clients of the measured matrix called it and were
answered "Unknown method".

What it must be, beyond the figure:

- **The vendor's figure, never an estimate.** The gateway's own estimate (characters over four) is
  deliberately high, for a reservation; a client compressing at it would compress too early. A
  model whose vendor counts nothing for this gateway is refused by name.
- **That model's.** A count is a fact about one tokenizer, so no fallback answers it.
- **Governed like a generation.** The whole request reaches the vendor, so it passes the same
  controls first — the use case, the rate limit, the pipeline's redactor — and leaves a row.
- **Booked as nothing.** Free at the vendor: nothing is reserved for it, and the row books no token
  and no request against a budget.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.serving import prepare
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    TextPart,
    ToolDeclaration,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.db.models import BudgetRead, ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini import GeminiUpstream
from aira_gateway.upstreams.mock import MockProvider

MODEL = "mock-1"
COUNT = f"/v1beta/models/{MODEL}:countTokens"
TEXT = {"role": "user", "parts": [{"text": "zähle diese fünf Wörter bitte"}]}


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0))


def _error(response: Any) -> str:
    return str(response.json()["error"]["message"])


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


# == the figure ====================================================================================


def test_a_conversation_is_counted_as_the_vendor_counts_it() -> None:
    """The mock counts a prompt as it reports one when it answers: five words."""
    with TestClient(_app()) as client:
        counted = client.post(COUNT, json={"contents": [TEXT]})
        answered = client.post(f"/v1beta/models/{MODEL}:generateContent", json={"contents": [TEXT]})

    assert counted.status_code == 200, counted.text
    assert counted.json() == {"totalTokens": 5}
    assert answered.json()["usageMetadata"]["promptTokenCount"] == 5


def test_a_whole_generation_request_is_counted_system_instruction_and_all() -> None:
    """The deprecated SDKs send the generation itself, model and settings included."""
    body = {
        "generateContentRequest": {
            "model": f"models/{MODEL}",
            "contents": [TEXT],
            "systemInstruction": {"parts": [{"text": "sei kurz"}]},
            "generationConfig": {},
            "safetySettings": [],
        }
    }
    with TestClient(_app()) as client:
        response = client.post(COUNT, json=body)

    assert response.status_code == 200, response.text
    assert response.json() == {"totalTokens": 7}


@pytest.mark.parametrize(
    "path",
    [f"/v1/models/{MODEL}:countTokens", f"/uc/uc/v1beta/models/{MODEL}:countTokens"],
    ids=["v1", "use-case-path"],
)
async def test_a_count_answers_wherever_a_generation_does(path: str) -> None:
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc"))
            await session.commit()
        response = client.post(path, json={"contents": [TEXT]})

    assert response.status_code == 200, response.text
    assert response.json()["totalTokens"] == 5


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ({"contents": [TEXT], "generateContentRequest": {"contents": [TEXT]}}, "send one"),
        ({}, "Nothing to count"),
        (
            {"generateContentRequest": {"model": "models/another", "contents": [TEXT]}},
            "'models/another'",
        ),
        ({"model": "models/another", "contents": [TEXT]}, "'models/another'"),
        ({"contents": [TEXT], "cachedContent": "cachedContents/x"}, "cachedContent"),
    ],
    ids=["both", "neither", "another-model-inside", "another-model", "unknown-field"],
)
def test_a_count_that_asks_two_things_or_none_is_refused_by_name(
    body: dict[str, Any], named: str
) -> None:
    with TestClient(_app()) as client:
        response = client.post(COUNT, json=body)

    assert response.status_code == 400, response.text
    assert named in _error(response)


def test_the_listing_offers_the_verb_where_the_vendor_counts() -> None:
    app = _app()
    app.state.providers = ProviderRegistry([MockProvider(MODEL), _Uncountable("openai-1")])
    with TestClient(app) as client:
        counts = client.get(f"/v1beta/models/{MODEL}").json()["supportedGenerationMethods"]
        cannot = client.get("/v1beta/models/openai-1").json()["supportedGenerationMethods"]

    assert "countTokens" in counts
    assert "countTokens" not in cannot


# == that model's, and the vendor's ================================================================


class _Uncountable:
    """A provider whose vendor counts nothing for this gateway, as an OpenAI-compatible one."""

    is_test_double = True
    sampling_controls = frozenset({"top_p"})

    def __init__(self, name: str) -> None:
        self.name = name

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self.name, "1", ("generateContent",), "test", region="")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=request.model,
            text="ok",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


def test_a_model_whose_vendor_counts_nothing_is_refused_rather_than_estimated() -> None:
    app = _app()
    app.state.providers = ProviderRegistry([_Uncountable("openai-1")])
    with TestClient(app) as client:
        response = client.post("/v1beta/models/openai-1:countTokens", json={"contents": [TEXT]})

    assert response.status_code == 400, response.text
    assert "counts no tokens" in _error(response)
    assert "not estimated" in _error(response)


def test_no_fallback_answers_for_a_model_that_cannot_be_counted() -> None:
    """Another model's tokenizer would answer a question nobody asked, under a 200."""
    app = _app()
    app.state.providers = ProviderRegistry([_Uncountable("openai-1"), MockProvider(MODEL)])

    class _Store:
        async def get(self, use_case: Any) -> Pipeline:
            return Pipeline(steps=(), fallback_models=(MODEL,))

    app.state.pipeline_store = _Store()
    with TestClient(app) as client:
        response = client.post("/v1beta/models/openai-1:countTokens", json={"contents": [TEXT]})

    assert response.status_code == 400, response.text
    assert "counts no tokens" in _error(response)


# == governed like a generation, booked as nothing ================================================


async def test_a_count_leaves_a_row_that_books_nothing() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.post(COUNT, json={"contents": [TEXT]})
        (row,) = await _rows(app)

    assert response.status_code == 200
    assert row.operation == "countTokens"
    assert row.status == 200
    assert row.model == MODEL
    assert (row.prompt_tokens, row.completion_tokens) == (0, 0)
    assert not row.cost_nanos


async def test_a_count_is_rate_limited_as_one_request_and_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One request at the rate limit — the pipeline runs for it — and nothing held back: a count
    near a limit would otherwise be refused for an answer it never produces."""
    weights: list[int] = []
    held: list[dict[str, Any]] = []
    guard, enforce = prepare.guard_before_work, prepare.enforce_pre_dispatch

    async def weighed(request: Any, *, units: int) -> Any:
        weights.append(units)
        return await guard(request, units=units)

    async def reserved(request: Any, **kwargs: Any) -> Any:
        held.append(kwargs)
        return await enforce(request, **kwargs)

    monkeypatch.setattr(prepare, "guard_before_work", weighed)
    monkeypatch.setattr(prepare, "enforce_pre_dispatch", reserved)
    with TestClient(_app()) as client:
        response = client.post(
            COUNT,
            json={
                "generateContentRequest": {
                    "contents": [TEXT],
                    "generationConfig": {"maxOutputTokens": 4000},
                }
            },
        )

    assert response.status_code == 200, response.text
    assert weights == [1]
    (kwargs,) = held
    assert kwargs["units"] == 0
    assert kwargs["max_output_tokens"] is None
    assert kwargs["answers_in_tokens"] is False
    assert not kwargs.get("input_texts")


async def test_counting_spends_nothing_of_a_request_budget() -> None:
    """A budget of one request a month: three counts, and the one answer is still allowed. A count
    booked as a request would have spent the month on asking how long a prompt is."""
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc"))
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    subject="",
                    period="month",
                    enabled=True,
                    limit_requests=1,
                )
            )
            await session.commit()
        counts = [
            client.post(COUNT, json={"contents": [TEXT]}, headers={"x-aira-use-case": "uc"})
            for _ in range(3)
        ]
        answered = client.post(
            f"/v1beta/models/{MODEL}:generateContent",
            json={"contents": [TEXT]},
            headers={"x-aira-use-case": "uc"},
        )
        over = client.post(
            f"/v1beta/models/{MODEL}:generateContent",
            json={"contents": [TEXT]},
            headers={"x-aira-use-case": "uc"},
        )

    assert [count.status_code for count in counts] == [200, 200, 200]
    assert answered.status_code == 200, answered.text
    assert over.status_code == 429, "the budget holds: the answer spent it"


async def test_a_count_checks_no_cap_on_an_answer_it_never_produces() -> None:
    """A generation request counted as written: its output cap above the model's ceiling refuses
    the generation, and is no reason to refuse counting its prompt."""
    app = _app()
    body = {"contents": [TEXT], "generationConfig": {"maxOutputTokens": 4000}}
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model=MODEL, approved=True, capabilities=["generate"], max_output_tokens=100
                )
            )
            await session.commit()
        counted = client.post(COUNT, json={"generateContentRequest": body})
        answered = client.post(f"/v1beta/models/{MODEL}:generateContent", json=body)

    assert answered.status_code == 400, "the cap is above the model's ceiling"
    assert counted.status_code == 200, counted.text


class _CountingRedactor:
    """Redacts the one name it knows when asked to, and counts what it is sent."""

    is_test_double = True

    def __init__(self) -> None:
        self.counted: list[str] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(MODEL, MODEL, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=MODEL,
            text=request.messages[-1].text.replace("Max Mustermann", "<PERSON>"),
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request: CanonicalRequest):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def count_tokens(self, request: CanonicalRequest, *, region: str = "") -> int:
        self.counted.append(request.last_user_text())
        return 3


async def test_a_count_reaches_the_vendor_through_the_use_cases_redactor() -> None:
    """The whole request is sent to be counted: a name the redactor removes from a generation
    must not reach the vendor in a count."""
    provider = _CountingRedactor()
    app = _app()
    registry = ProviderRegistry([provider])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)

    class _Store:
        async def get(self, use_case: str | None) -> Pipeline:
            step = PipelineStep(type=StepType.PII_FILTER, config={"model": MODEL})
            return Pipeline(steps=(step,), fallback_models=())

    app.state.pipeline_store = _Store()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=[MODEL]))
            session.add(ModelRead(model=MODEL, approved=True, capabilities=["generate"]))
            await session.commit()
        response = client.post(
            COUNT,
            json={"contents": [{"role": "user", "parts": [{"text": "Brief an Max Mustermann"}]}]},
            headers={"x-aira-use-case": "uc"},
        )

    assert response.status_code == 200, response.text
    assert provider.counted == ["Brief an <PERSON>"]


class _Counted(_CountingRedactor):
    """A model, not a double: the installation's approval and the use case's release apply."""

    is_test_double = False

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("gemini-x", "gemini-x", ("generateContent",))]


async def test_a_count_for_a_model_the_use_case_was_not_given_is_refused_as_its_answer_is() -> None:
    """The text reaches the vendor of a model the use case may not call: refused where a
    generation is, for the same reason."""
    app = _app()
    provider = _Counted()
    app.state.providers = ProviderRegistry([provider])
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=["another"]))
            session.add(ModelRead(model="gemini-x", approved=True, capabilities=["generate"]))
            await session.commit()
        counted = client.post(
            "/v1beta/models/gemini-x:countTokens",
            json={"contents": [TEXT]},
            headers={"x-aira-use-case": "uc"},
        )
        answered = client.post(
            "/v1beta/models/gemini-x:generateContent",
            json={"contents": [TEXT]},
            headers={"x-aira-use-case": "uc"},
        )

    assert counted.status_code == answered.status_code != 200, counted.text
    assert "has not been released to use case 'uc'" in _error(counted)
    assert provider.counted == []


# == Google AI Studio counts as its API does =======================================================


def _ai_studio(answer: dict[str, Any], seen: list[httpx.Request]) -> GeminiUpstream:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)

    client = httpx.AsyncClient(
        base_url="https://generativelanguage.example/v1beta",
        transport=httpx.MockTransport(handle),
    )
    return GeminiUpstream("test-key", ["gemini-x"], client)


def _declaring() -> CanonicalRequest:
    return CanonicalRequest(
        model="gemini-x",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, parts=[TextPart(text="sei kurz")]),
            CanonicalMessage(role=Role.USER, parts=[TextPart(text="hallo")]),
        ],
        tools=(
            ToolDeclaration(
                name="lookup",
                parameters=parse_schema(
                    {"type": "OBJECT", "properties": {"q": {"type": "STRING"}}}
                ),
            ),
        ),
        temperature=0.3,
    )


async def test_ai_studio_counts_the_instruction_and_the_functions_with_the_turns() -> None:
    """The Gemini API counts a system instruction and declared functions only inside the
    generation they belong to; the sampling settings are no input and are not sent."""
    seen: list[httpx.Request] = []
    adapter = _ai_studio({"totalTokens": 42}, seen)

    assert await adapter.count_tokens(_declaring()) == 42
    (sent,) = seen
    assert sent.url.path.endswith("/models/gemini-x:countTokens")
    body = json.loads(sent.content)
    inner = body["generateContentRequest"]
    assert inner["model"] == "models/gemini-x"
    assert inner["systemInstruction"]["parts"][0]["text"] == "sei kurz"
    assert inner["tools"][0]["functionDeclarations"][0]["name"] == "lookup"
    assert "generationConfig" not in inner
    assert sent.headers["x-goog-api-key"] == "test-key"


@pytest.mark.parametrize("answer", [{}, {"totalTokens": "42"}, {"totalTokens": -1}])
async def test_a_count_without_a_figure_is_the_vendors_fault(answer: dict[str, Any]) -> None:
    adapter = _ai_studio(answer, [])

    with pytest.raises(UpstreamError) as failed:
        await adapter.count_tokens(_declaring())
    assert failed.value.status_code == 502
