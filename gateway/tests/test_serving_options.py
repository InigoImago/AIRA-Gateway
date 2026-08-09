"""Thinking, structured output and batch embedding, end to end through a surface.

The unit tests pin the resolution and the validation. What only shows up here is whether the
**controls actually see them** — the reservation, the dispatch chain and the audit are three places
a correctly-resolved setting can still be dropped on the way past, and none of them would say so.

Two properties carry most of the weight:

- `FRD-112` §5.3, the routing interaction. Checking the capability against the model the *caller*
  named protects nothing once a chain exists, and the test below is written to fail against that
  implementation rather than merely to pass against this one.
- `FRD-111` FR-5, the reservation. A 20 000-token thinking budget that reserves like a sentence is
  a spend limit that cannot see the most expensive knob on the request.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.models import ThinkingMode
from aira_gateway.app import create_app
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    Thinking,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.requirements import StructuredOutputSupported, ThinkingHonoured, permits
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

SCHEMA = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}}}


def _app(**settings: Any):  # noqa: ANN201
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _catalogue(app, model: str = "mock-1", **fields: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, **fields))
        await session.commit()


def _body(**config: Any) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "generationConfig": config,
    }


# == the Gemini surface accepts both spellings ===================================================


async def test_googles_thinking_budget_reaches_the_model() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["limited"], "min_tokens": 128, "max_tokens": 8192},
        )
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"thinkingBudget": 2048}),
        )

    assert response.status_code == 200, response.text
    assert "thinking:limited budget=2048" in response.text


@pytest.mark.parametrize(("budget", "expected"), [(0, "thinking:disabled"), (-1, "thinking:auto")])
async def test_googles_two_sentinels_are_read_as_the_modes_they_are(
    budget: int, expected: str
) -> None:
    """`0` is off and `-1` is the model's own choice. Reading them as a `limited` budget of zero
    would ask a provider for zero thinking tokens, which is a different request entirely."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app, capabilities=["generate", "thinking"], thinking={"modes": ["disabled", "auto"]}
        )
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"thinkingBudget": budget}),
        )

    assert response.status_code == 200, response.text
    assert expected in response.text


async def test_the_canonical_spelling_reaches_the_abstract_levels() -> None:
    """Google's numeric field cannot express "medium", and the level's budget is per model — so
    the canonical form is the only way a Gemini-surface caller reaches one."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["medium"], "levels": {"medium": 4096}},
        )
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"mode": "medium"}),
        )

    assert response.status_code == 200, response.text
    assert "thinking:medium budget=4096" in response.text


async def test_both_spellings_at_once_is_a_400_rather_than_a_precedence_rule() -> None:
    """A silent precedence rule is not something a caller can predict from the outside."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "thinking"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"thinkingBudget": 100, "mode": "auto"}),
        )

    assert response.status_code == 400


async def test_an_unknown_mode_carries_the_predecessors_code_in_the_message() -> None:
    """Google's envelope has no field for an error code, so it travels in the message rather than
    being invented into the envelope — this surface stays Gemini-shaped."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "thinking"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"mode": "ludicrous"}),
        )

    assert response.status_code == 400
    assert "INVALID_THINKING_MODE" in response.json()["error"]["message"]


# == structured output ===========================================================================


async def test_a_schema_request_returns_a_document_and_the_json_media_type() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(responseMimeType="application/json", responseSchema=SCHEMA),
        )

    assert response.status_code == 200, response.text
    import json

    document = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
    assert set(document) == {"answer"}


async def test_a_bound_is_enforced_at_the_surface() -> None:
    app = _app(max_response_schema_depth=2)
    deep = {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}}
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent", json=_body(responseSchema=deep)
        )

    assert response.status_code == 400
    assert "nests deeper" in response.json()["error"]["message"]


# == the routing interaction (FRD-112 §5.3) ======================================================


class _Provider:
    #: A test double, like `MockProvider` (`FRD-307`): it serves invented models, so the
    #: catalogue-and-approve requirement does not apply to it.
    is_test_double = True

    def __init__(self, *models: str) -> None:
        self._models = models

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(m, m, ("generateContent",), "mock", "mock", "") for m in self._models]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=request.model,
            text=f"answered by {request.model}",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError


class _Catalog:
    """A catalog stub whose only job is to say which models declare which capability."""

    def __init__(self, declarations: dict[str, Any]) -> None:
        self._declarations = declarations

    async def declaration(self, model: str):  # noqa: ANN201
        from aira_gateway.catalog import ModelDeclaration

        return self._declarations.get(model, ModelDeclaration(name=model))


def _declaring(*capabilities: str, **fields: Any):  # noqa: ANN202
    from aira_common.models import parse_capabilities
    from aira_gateway.catalog import ModelDeclaration

    return lambda name: ModelDeclaration(
        name=name, declared=True, capabilities=parse_capabilities(list(capabilities)), **fields
    )


def _request(model: str, **fields: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model=model, messages=[CanonicalMessage(role=Role.USER, text="hi")], **fields
    )


async def test_a_fallback_candidate_without_structured_output_is_skipped() -> None:
    """The test that justifies the design. Against a "check the model the caller named"
    implementation this passes vacuously — the primary declares the capability — and the request
    is then answered in **prose** by the fallback, which is the failure. Here it is skipped."""
    registry = ProviderRegistry([_Provider("primary", "plain", "capable")])
    catalog = _Catalog(
        {
            "primary": _declaring("generate", "structured_output")("primary"),
            "plain": _declaring("generate")("plain"),
            "capable": _declaring("generate", "structured_output")("capable"),
        }
    )
    from aira_gateway.core.schema import parse as parse_schema

    request = _request("plain", response_schema=parse_schema(SCHEMA))
    dispatched = await dispatch_with_fallback(
        registry, request, ("capable",), permits=permits([StructuredOutputSupported(catalog)])
    )

    assert dispatched.response.model == "capable"
    assert [entry.model for entry in dispatched.skipped] == ["plain"]
    assert "structured output" in dispatched.skipped[0].reason


async def test_a_chain_with_no_capable_candidate_fails_rather_than_answering_in_prose() -> None:
    """Returning the wrong *shape* is worse than returning an error, because only the error is
    noticed — the prose surfaces as a parse failure in somebody else's application, days later."""
    registry = ProviderRegistry([_Provider("a", "b")])
    catalog = _Catalog({name: _declaring("generate")(name) for name in ("a", "b")})
    from aira_gateway.core.schema import parse as parse_schema

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(
            registry,
            _request("a", response_schema=parse_schema(SCHEMA)),
            ("b",),
            permits=permits([StructuredOutputSupported(catalog)]),
        )

    assert "schema" in str(caught.value)


async def test_a_fallback_candidate_that_cannot_think_that_hard_is_skipped() -> None:
    """Same rule, different property: less reasoning than was asked for is not an error, it is a
    worse answer with a 200 on it."""
    registry = ProviderRegistry([_Provider("narrow", "wide")])
    catalog = _Catalog(
        {
            "narrow": _declaring(
                "generate", "thinking", thinking={"modes": ["limited"], "max_tokens": 512}
            )("narrow"),
            "wide": _declaring(
                "generate", "thinking", thinking={"modes": ["limited"], "max_tokens": 32_000}
            )("wide"),
        }
    )
    setting = Thinking(mode=ThinkingMode.LIMITED, tokens=8192)

    dispatched = await dispatch_with_fallback(
        registry,
        _request("narrow", thinking=setting),
        ("wide",),
        permits=permits([ThinkingHonoured(catalog, setting)]),
    )

    assert dispatched.response.model == "wide"


# == the reservation sees the budget (FRD-111 FR-5) ==============================================


class _RecordingBudgets:
    """Captures what was reserved, which is the only way to see an estimate at all."""

    def __init__(self) -> None:
        self.estimates: list[Amounts] = []
        self.released = 0
        self.settled: list[int] = []

    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        from aira_gateway.budgets.service import Reservation

        self.estimates.append(estimated)
        return Reservation()

    # `FRD-125c` added a pre-pipeline check to the real service. Inherited rather than stubbed out:
    # a stand-in more permissive than the thing it replaces is how a control comes to be tested
    # against something that cannot refuse. These stands-in carry no budgets, so it returns at once.
    refuse_if_exhausted = BudgetService.refuse_if_exhausted

    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None, requests=1):  # noqa: ANN001, ANN201, E501
        reservation.resolved = True
        self.settled.append(requests)

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True
        self.released += 1

    @property
    def hold(self):  # noqa: ANN201
        from aira_gateway.budgets.service import BudgetService

        return BudgetService.hold.__get__(self)


async def test_a_large_thinking_budget_reserves_materially_more() -> None:
    """Thinking tokens are billed as output tokens and can be an order of magnitude larger than the
    answer. A reservation that ignored them would be a spend limit blind to the expensive half."""
    app = _app()
    budgets = _RecordingBudgets()
    app.state.budgets = budgets
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["limited", "disabled"], "min_tokens": 128, "max_tokens": 32_000},
        )
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(thinkingConfig={"thinkingBudget": 20_000}),
        )
        client.post("/v1beta/models/mock-1:generateContent", json=_body())

    with_thinking, without = budgets.estimates
    assert with_thinking.tokens - without.tokens == 20_000


# == batch embedding through the surface =========================================================


async def test_batch_embed_returns_a_vector_per_text_and_is_recorded_as_many_requests() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"], embedding={"supports_batch": True})
        response = client.post(
            "/v1beta/models/mock-1:batchEmbedContents",
            json={
                "requests": [
                    {"content": {"parts": [{"text": "one"}]}},
                    {"content": {"parts": [{"text": "two"}]}},
                ]
            },
        )

        assert response.status_code == 200, response.text
        assert len(response.json()["embeddings"]) == 2

        async with app.state.db_sessionmaker() as session:
            row = (await session.execute(select(RequestLog))).scalars().one()
        # The audit says which verb ran; a batch recorded as `embedContent` would make "how much
        # of our embedding traffic is batched" unanswerable from the data.
        assert row.operation == "batchEmbedContents"


async def test_a_batch_mixing_task_types_is_refused_rather_than_flattened() -> None:
    """One call carries one task type, because that is what is metered, validated and recorded.
    Serving a mixed batch would put one task type on an audit row for vectors built with two."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["embed"],
            embedding={"supports_batch": True, "task_types": ["RETRIEVAL_QUERY", "CLUSTERING"]},
        )
        response = client.post(
            "/v1beta/models/mock-1:batchEmbedContents",
            json={
                "requests": [
                    {"content": {"parts": [{"text": "a"}]}, "taskType": "RETRIEVAL_QUERY"},
                    {"content": {"parts": [{"text": "b"}]}, "taskType": "CLUSTERING"},
                ]
            },
        )

    assert response.status_code == 400
    assert "one task type" in response.json()["error"]["message"]


async def test_an_attachment_in_an_embedding_request_is_still_refused() -> None:
    """Embedding a document means chunking it, which is the consumer's decision. Embedding the
    prompt and dropping the file would return a vector confidently about the wrong thing."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"])
        response = client.post(
            "/v1beta/models/mock-1:embedContent",
            json={
                "content": {
                    "parts": [{"inlineData": {"mimeType": "application/pdf", "data": "JVBERi0="}}]
                }
            },
        )

    assert response.status_code == 400


async def test_a_truncated_document_is_refused_rather_than_returned_as_data() -> None:
    """FR-6. A document cut off at the output cap is still valid-looking JSON right up to where it
    stops, and the two ways a schema request goes wrong — truncation, and a model that answered in
    prose — are indistinguishable from the outside. Handing either back as the requested shape
    gives somebody else's code a parse error, or worse a *successful* parse of half the data."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(responseSchema=SCHEMA, maxOutputTokens=1),
        )

    assert response.status_code == 502
    assert "max_tokens" in response.json()["error"]["message"]


async def test_a_truncated_ordinary_answer_is_still_returned() -> None:
    """The check is about the *schema*, not about truncation. Prose cut off at the caller's own
    cap is what they asked for, and refusing it would break every bounded generation."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent", json=_body(maxOutputTokens=1)
        )

    assert response.status_code == 200


async def test_a_refused_document_releases_its_reservation() -> None:
    """The refusal happens after dispatch, inside the hold — so it must give the budget back
    rather than charging a use case for an answer it never received."""
    app = _app()
    budgets = _RecordingBudgets()
    app.state.budgets = budgets
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_body(responseSchema=SCHEMA, maxOutputTokens=1),
        )
        assert response.status_code == 502
        assert budgets.released == 1


class _WeighingLimiter:
    """Records what each request weighed. The bucket arithmetic is tested elsewhere; what is only
    visible here is whether the *route* tells the limiter the truth about the request's size."""

    def __init__(self) -> None:
        self.weights: list[int] = []

    async def check(self, use_case, subject, units=1, *, extra=()):  # noqa: ANN001, ANN201
        # `extra` is part of the real signature (`FRD-503`: a throttle is an additional
        # bucket). A stand-in narrower than the thing it replaces is how this project has
        # already lost a defect once — CLAUDE.md §3.
        self.weights.append(units)


async def test_a_batch_reaches_the_rate_limiter_as_the_many_requests_it_is() -> None:
    """`FRD-113` FR-6, at the place it can actually be bypassed. A batch of 500 admitted as one
    request turns a limit of 10 per minute into 5 000 texts per minute — intact on paper, gone in
    practice. Against a `check(use_case, subject)` route this records 1 and fails."""
    app = _app()
    limiter = _WeighingLimiter()
    app.state.rate_limits = limiter
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"], embedding={"supports_batch": True})
        response = client.post(
            "/v1beta/models/mock-1:batchEmbedContents",
            json={"requests": [{"content": {"parts": [{"text": t}]}} for t in "abcde"]},
        )

    assert response.status_code == 200, response.text
    assert limiter.weights == [5]


async def test_a_batch_reserves_and_settles_as_the_many_requests_it_is() -> None:
    """The same argument for the budget: a request-count limit that cannot see batched traffic is
    a request-count limit that a batching caller never meets."""
    app = _app()
    budgets = _RecordingBudgets()
    app.state.budgets = budgets
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"], embedding={"supports_batch": True})
        response = client.post(
            "/v1beta/models/mock-1:batchEmbedContents",
            json={"requests": [{"content": {"parts": [{"text": t}]}} for t in "abcde"]},
        )

    assert response.status_code == 200, response.text
    assert budgets.estimates[0].requests == 5
    assert budgets.settled == [5]


async def test_a_batch_verb_needs_the_embedding_capability_not_the_generation_one() -> None:
    """The verb set is a set for a reason: a check written against one verb name would demand
    *generation* of the other — refusing every batch against an embedding-only model, and
    accepting one against a model that cannot embed at all. That is the `:embedContent` bypass
    with one more verb in it."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"], embedding={"supports_batch": True})
        response = client.post(
            "/v1beta/models/mock-1:batchEmbedContents",
            json={"requests": [{"content": {"parts": [{"text": "a"}]}}]},
        )

    assert response.status_code == 400
    assert "does not support embedding" in response.json()["error"]["message"]
