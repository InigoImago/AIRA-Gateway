"""A streamed request uses the use case's chain, for as long as nothing has reached the caller.

**What this is about.** The chain `ADR-0012` §3 gave a use case was applied by one verb. A streamed
request resolved exactly one adapter, because the chain returns a *finished* response and a stream
cannot be made out of one — so the availability a second model is configured for did not exist on
the verb every chat client and every coding assistant uses. A model that was rate-limited for ten
minutes took every streamed request of that use case with it, while the same request sent to
`:generateContent` was served by the next candidate.

**Why it is possible at all**: a streaming adapter issues its HTTP request on the first
``__anext__``, not when the iterator is built. Pulling one chunk is therefore both the proof that a
candidate can serve and the last moment at which replacing it is invisible to the caller
(`FRD-302` FR-6, `ADR-0032`).

**And what it fixes on the way**: the response no longer begins before a model has produced
something, so a streamed request nothing can serve is a status again. It used to be `200` with the
body `[]` — indistinguishable from a model that had nothing to say.

After the first chunk the stream is committed, and that is asserted here too: text from two models
spliced into one answer would be worse than the failure it hid.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.observability import model_call_attributes
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.dispatch import NoCapableModel, Routing, stream_with_fallback
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)


class _Server:
    """An adapter that streams two chunks and says which machine answered."""

    def __init__(self, model: str) -> None:
        self._model = model
        #: Whether this adapter's stream was closed rather than exhausted — what a passed-over
        #: candidate must leave behind, or its connection is held until the garbage collector runs.
        self.closed = False

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                self._model, self._model, ("generateContent", "streamGenerateContent"), "acme"
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(model=request.model, text=f"by:{self._model}", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        try:
            yield CanonicalChunk(text_delta=f"by:{self._model}")
            yield CanonicalChunk(text_delta="-done", usage=_USAGE, finish_reason="stop")
        finally:
            # `aclose()` on a suspended generator raises `GeneratorExit` here; an exhausted one
            # passes through. The flag therefore says "closed early", which is the question.
            self.closed = True

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Unreachable(_Server):
    """An adapter whose stream fails **before** its first chunk — the outage a chain is for."""

    def __init__(self, model: str, status: int | None = 503) -> None:
        super().__init__(model)
        self._status = status
        self.attempts = 0

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        self.attempts += 1
        raise UpstreamError(f"{self._model} is unavailable", self._status)
        yield  # pragma: no cover — unreachable, and what makes this an async generator


class _BreaksAfterTheFirstChunk(_Server):
    """An adapter that answers and then fails. Past the point of no return."""

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        yield CanonicalChunk(text_delta=f"by:{self._model}", usage=_USAGE)
        raise UpstreamError("the connection dropped mid-answer", None)


class _Silent(_Server):
    """An adapter whose stream ends with no chunk at all: a vendor's own refusal looks like it."""

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        return
        yield  # pragma: no cover — unreachable, and what makes this an async generator


def _request(model: str = "primary-1") -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],  # type: ignore[list-item]
    )


async def _text(chunks: AsyncIterator[CanonicalChunk]) -> str:
    return "".join([chunk.text_delta or "" async for chunk in chunks])


# == the chain itself =============================================================================


async def test_a_candidate_that_fails_before_its_first_chunk_is_replaced() -> None:
    """The case the feature exists for. The caller gets the second model's answer, whole."""
    down, up = _Unreachable("primary-1"), _Server("backup-1")
    registry = ProviderRegistry([down, up])

    streamed = await stream_with_fallback(registry, _request(), ("backup-1",))

    assert streamed.model == "backup-1"
    assert streamed.candidate_index == 1
    assert await _text(streamed.chunks) == "by:backup-1-done"
    # The attempt is reported, with the upstream's own reason and its status.
    assert [(entry.model, entry.reason) for entry in streamed.failed] == [
        ("primary-1", "primary-1 is unavailable (HTTP 503)")
    ]
    assert streamed.skipped == []


async def test_the_first_candidate_is_tried_first_and_keeps_the_answer() -> None:
    """A chain is not a preference for the fallback: with the primary up, nothing else is asked."""
    up, spare = _Server("primary-1"), _Unreachable("backup-1")
    registry = ProviderRegistry([up, spare])

    streamed = await stream_with_fallback(registry, _request(), ("backup-1",))

    assert (streamed.model, streamed.candidate_index) == ("primary-1", 0)
    assert await _text(streamed.chunks) == "by:primary-1-done"
    assert spare.attempts == 0


async def test_a_candidate_that_fails_after_its_first_chunk_is_not_replaced() -> None:
    """**The limit, asserted.** The caller already has text from this model; continuing it from
    another would splice two answers and nothing downstream could tell."""
    first, spare = _BreaksAfterTheFirstChunk("primary-1"), _Server("backup-1")
    registry = ProviderRegistry([first, spare])

    streamed = await stream_with_fallback(registry, _request(), ("backup-1",))

    assert streamed.model == "primary-1"
    with pytest.raises(UpstreamError, match="mid-answer"):
        await _text(streamed.chunks)


async def test_the_stream_of_a_passed_over_candidate_is_closed() -> None:
    """An open HTTP response holds its connection out of the adapter's pool until the garbage
    collector gets to it — under the outage a chain exists for, that is the worst moment for it.

    **The first version of this test could not fail**, and the mutation harness said so (`MF3`
    survived). It used an async generator that raised: a generator is *finished* by the raise, so
    its `finally` runs whether or not anybody closed it, and the assertion was about Python rather
    than about the chain. `Upstream.stream_generate` is typed as an `AsyncIterator`, and an adapter
    written as one — a class holding a response, not a generator — releases it on `aclose()` and
    not before. That is the shape this asks about.
    """

    class _HeldOpen:
        """An iterator whose `__anext__` fails and whose response only `aclose()` releases."""

        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self) -> _HeldOpen:
            return self

        async def __anext__(self) -> CanonicalChunk:
            raise UpstreamError("primary-1 is unavailable", 503)

        async def aclose(self) -> None:
            self.closed = True

    held = _HeldOpen()

    class _HoldsAStream(_Server):
        def stream_generate(self, request: CanonicalRequest) -> Any:  # noqa: ANN401
            return held

    down, up = _HoldsAStream("primary-1"), _Server("backup-1")
    streamed = await stream_with_fallback(ProviderRegistry([down, up]), _request(), ("backup-1",))

    assert streamed.model == "backup-1"
    assert held.closed, "the abandoned attempt's stream was left open"
    # And the one that answered is **not** closed behind the surface's back.
    assert not up.closed
    await _text(streamed.chunks)


async def test_a_stream_with_no_chunks_is_an_answer_and_not_an_outage() -> None:
    """A finish reason and no text is how a vendor's own safety filter refuses. Falling back there
    would pay three models to refuse the same request three times."""
    silent, spare = _Silent("primary-1"), _Unreachable("backup-1")
    registry = ProviderRegistry([silent, spare])

    streamed = await stream_with_fallback(registry, _request(), ("backup-1",))

    assert (streamed.model, streamed.candidate_index) == ("primary-1", 0)
    assert await _text(streamed.chunks) == ""
    assert spare.attempts == 0, "an empty answer was treated as a failure"


async def test_a_condition_skips_a_candidate_without_calling_it() -> None:
    """The conditions are asked per hop, exactly as for the non-streamed chain: a candidate that
    would answer a *different* question is not tried."""
    refused, up = _Unreachable("primary-1"), _Server("backup-1")
    registry = ProviderRegistry([refused, up])

    async def permits(model: str) -> str | None:
        return "not released to this use case" if model == "primary-1" else None

    streamed = await stream_with_fallback(registry, _request(), ("backup-1",), permits=permits)

    assert streamed.model == "backup-1"
    assert refused.attempts == 0, "a candidate a condition excluded was called anyway"
    assert [(entry.model, entry.reason) for entry in streamed.skipped] == [
        ("primary-1", "not released to this use case")
    ]
    assert streamed.failed == []


async def test_every_candidate_failing_raises_the_upstreams_own_error() -> None:
    """A chain that was *tried* and failed is an outage the caller may usefully retry — not a
    configuration fault, which is what `NoCapableModel` means."""
    registry = ProviderRegistry([_Unreachable("primary-1"), _Unreachable("backup-1", 429)])

    with pytest.raises(UpstreamError) as failure:
        await stream_with_fallback(registry, _request(), ("backup-1",))

    assert failure.value.status_code == 429, "the last attempt's status is the one reported"


async def test_a_chain_with_nothing_to_offer_says_what_it_skipped() -> None:
    """Every candidate excluded is a configuration an operator can fix, and the refusal names
    each one."""
    registry = ProviderRegistry([_Server("primary-1"), _Server("backup-1")])

    async def permits(model: str) -> str | None:
        return f"{model} is not approved"

    with pytest.raises(NoCapableModel, match="primary-1 is not approved"):
        await stream_with_fallback(registry, _request(), ("backup-1",), permits=permits)


async def test_the_addressing_moves_with_the_model() -> None:
    """`ADR-0011`: a hop that took the fallback's provider and kept the primary's addressing sent
    it to the primary's region. The catalogue is asked per candidate, for a stream as well."""
    seen: list[dict[str, Any]] = []

    class _Recording(_Server):
        async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
            seen.append(dict(request.addressing))
            yield CanonicalChunk(text_delta="ok", usage=_USAGE, finish_reason="stop")

    async def routing_of(model: str) -> Routing:
        return Routing(provider="acme", addressing={"region": f"{model}-region"})

    registry = ProviderRegistry([_Unreachable("primary-1"), _Recording("backup-1")])
    streamed = await stream_with_fallback(
        registry, _request(), ("backup-1",), routing_of=routing_of
    )
    await _text(streamed.chunks)

    assert seen == [{"region": "backup-1-region"}]


# == through the surfaces =========================================================================


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(chain: tuple[str, ...], *providers: object) -> Any:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(Pipeline(fallback_models=chain))
    return app


async def _catalogue(app: Any, *models: str) -> None:
    """Catalogue each model, approved, with the numeric id the KIRA surface addresses it by."""
    async with app.state.db_sessionmaker() as session:
        for number, model in enumerate(models, start=9001):
            session.add(
                ModelRead(model=model, numeric_id=number, approved=True, capabilities=["generate"])
            )
        await session.commit()


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


async def test_a_streamed_request_is_served_by_the_fallback_and_recorded_as_such() -> None:
    """End to end on the Gemini surface: the caller gets an answer, and the row says whose."""
    app = _app(("backup-1",), _Unreachable("primary-1"), _Server("backup-1"))
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1")
        response = client.post("/v1beta/models/primary-1:streamGenerateContent", json=_BODY)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "by:backup-1" in response.text
    assert len(rows) == 1
    row = rows[0]
    # Recorded, priced and reported under the model that answered — not the one the caller named.
    assert (row.requested_model, row.model) == ("primary-1", "backup-1")
    assert row.model_selection is not None, "nothing says a substitution happened"
    # And **why** the primary was replaced, which is the half that was missing until `ADR-0032`.
    assert {
        (decision.get("action"), decision.get("to")) for decision in row.pipeline_decisions or []
    } == {("failed", "primary-1")}
    assert "unavailable" in str(row.pipeline_decisions)


async def test_a_streamed_request_nothing_can_serve_is_a_status_with_a_reason() -> None:
    """`FRD-302` FR-6a. It used to be `200` and `[]`, because the response had already begun."""
    app = _app(("backup-1",), _Unreachable("primary-1"), _Unreachable("backup-1"))
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1")
        response = client.post("/v1beta/models/primary-1:streamGenerateContent", json=_BODY)
        rows = await _rows(app)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["status"] == "UNAVAILABLE"
    assert [(row.status, row.outcome) for row in rows] == [(503, "upstream_error")]


async def test_the_kira_surface_falls_back_too() -> None:
    """The predecessor's SSE contract, on the same chain: two surfaces, one mechanism."""
    app = _app(("backup-1",), _Unreachable("primary-1"), _Server("backup-1"))
    # The predecessor addresses a model by number, which is why `_catalogue` gives it one.
    body = {"request": {"parts": [{"text": "hello"}]}, "model_id": 9001}
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1")
        response = client.post("/kira/api/external/streaming-chat", json=body)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "by:backup-1" in response.text
    # The terminal `completed` event names the model that answered.
    assert "backup-1" in response.text
    assert [(row.requested_model, row.model) for row in rows] == [("primary-1", "backup-1")]


async def test_each_streamed_attempt_is_marked_with_the_model_it_reached_for() -> None:
    """`FRD-619`: the record a delivery channel forwards for a model call names the model. For a
    chain that has to be **per attempt**, or a fallback's access record names the model that was
    down rather than the one that answered.

    Asked where the mark can be read — inside the call, which for a stream is the first
    ``__anext__``, because that is the ``__anext__`` that issues the HTTP request.
    """
    marked: list[dict[str, object]] = []

    class _Marked(_Server):
        async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
            marked.append(dict(model_call_attributes() or ()))
            yield CanonicalChunk(text_delta=f"by:{self._model}", usage=_USAGE, finish_reason="stop")

    class _MarkedAndDown(_Marked):
        async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
            marked.append(dict(model_call_attributes() or ()))
            raise UpstreamError(f"{self._model} is unavailable", 503)
            yield  # pragma: no cover — unreachable, and what makes this an async generator

    registry = ProviderRegistry([_MarkedAndDown("primary-1"), _Marked("backup-1")])
    streamed = await stream_with_fallback(registry, _request(), ("backup-1",))
    await _text(streamed.chunks)

    assert marked == [
        {"aira.model": "primary-1", "aira.model_call.purpose": "serve"},
        {"aira.model": "backup-1", "aira.model_call.purpose": "serve"},
    ], (
        "an attempt was marked with the model that was asked for rather than the one it tried, so "
        "its access record names a model it never called"
    )


# == what a passed-over candidate leaves behind ===================================================


async def test_an_iterator_with_nothing_to_close_is_passed_over_cleanly() -> None:
    """`Upstream.stream_generate` is typed as an `AsyncIterator`, and the plainest one has no
    `aclose` at all. The chain must not require a method the contract does not."""

    class _Bare:
        def __aiter__(self) -> _Bare:
            return self

        async def __anext__(self) -> CanonicalChunk:
            raise UpstreamError("primary-1 is unavailable", 503)

    class _HasNoClose(_Server):
        def stream_generate(self, request: CanonicalRequest) -> Any:  # noqa: ANN401
            return _Bare()

    streamed = await stream_with_fallback(
        ProviderRegistry([_HasNoClose("primary-1"), _Server("backup-1")]), _request(), ("backup-1",)
    )

    assert streamed.model == "backup-1"


async def test_a_close_that_fails_does_not_cost_the_caller_their_answer() -> None:
    """Cleanup must not replace the thing it was cleaning up after. The caller is waiting for the
    next candidate; an exception from `aclose()` would hand them the failure of a tidy-up instead
    of the answer a chain exists to produce."""

    class _RefusesToClose:
        def __aiter__(self) -> _RefusesToClose:
            return self

        async def __anext__(self) -> CanonicalChunk:
            raise UpstreamError("primary-1 is unavailable", 503)

        async def aclose(self) -> None:
            raise RuntimeError("the socket is already gone")

    class _Stubborn(_Server):
        def stream_generate(self, request: CanonicalRequest) -> Any:  # noqa: ANN401
            return _RefusesToClose()

    streamed = await stream_with_fallback(
        ProviderRegistry([_Stubborn("primary-1"), _Server("backup-1")]), _request(), ("backup-1",)
    )

    assert streamed.model == "backup-1"
    assert await _text(streamed.chunks) == "by:backup-1-done"


async def test_a_caller_who_stops_reading_closes_the_stream_that_answered() -> None:
    """The chain hands the surface an iterator of its own making, and a wrapper is a place a
    `GeneratorExit` can be swallowed. A caller that hangs up must still release the upstream's
    response — the leak the surfaces were careful about before the wrapper existed."""
    answering = _Server("primary-1")
    streamed = await stream_with_fallback(ProviderRegistry([answering]), _request(), ())

    chunks = streamed.chunks
    assert (await anext(chunks)).text_delta == "by:primary-1"
    await chunks.aclose()  # type: ignore[attr-defined]

    assert answering.closed, "the adapter's stream outlived the caller who was reading it"


# == the governance half, through the surface =====================================================


async def _release(app: Any, slug: str, models: list[str]) -> None:
    from aira_gateway.db.models import UseCaseRead

    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, allowed_models=models))
        await session.commit()


async def test_a_streamed_request_for_a_withheld_model_is_served_by_the_chain() -> None:
    """**Worth stating because it is surprising**, and it is the non-streamed chain's behaviour
    arriving on the streamed verb rather than a new decision.

    A caller naming a model their use case was never released (`FRD-308`) is not served *that*
    model — the candidate is skipped by name. What they get is the use case's own chain, and the
    row says both halves: `skipped` with the reason, and the model that did answer. The
    alternative — refusing because candidate 0 was withheld — would make a chain useless for the
    case an operator most wants it for: a caller pinning a model they should not be using.
    """
    app = _app(("backup-1",), _Server("withheld-1"), _Server("backup-1"))
    with TestClient(app) as client:
        await _catalogue(app, "withheld-1", "backup-1")
        await _release(app, "uc", ["backup-1"])
        response = client.post(
            "/v1beta/models/withheld-1:streamGenerateContent",
            json=_BODY,
            headers={"X-AIRA-Use-Case": "uc"},
        )
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "by:backup-1" in response.text
    (row,) = rows
    assert (row.requested_model, row.model) == ("withheld-1", "backup-1")
    assert [
        (decision.get("action"), decision.get("to")) for decision in row.pipeline_decisions or []
    ] == [("skipped", "withheld-1")]
    assert "released" in str(row.pipeline_decisions)


async def test_a_streamed_fallback_is_priced_as_the_model_that_answered() -> None:
    """The row is evidence about spend, and a chain makes the cheap question wrong: a fallback
    answered, so the price that applies is **its** price. Priced from `trail.served_model`'s
    catalogue entry, which is the model the chain chose and not the one the caller typed."""
    from aira_common.money import to_nanos
    from aira_gateway.db.models import ModelRead as Model

    app = _app(("backup-1",), _Unreachable("primary-1"), _Server("backup-1"))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            # A tenfold difference, so a row priced from the wrong model is unmistakable.
            session.add(
                Model(
                    model="primary-1",
                    numeric_id=9001,
                    approved=True,
                    capabilities=["generate"],
                    input_price_per_million_nanos=to_nanos("10.00"),
                    output_price_per_million_nanos=to_nanos("10.00"),
                )
            )
            session.add(
                Model(
                    model="backup-1",
                    numeric_id=9002,
                    approved=True,
                    capabilities=["generate"],
                    input_price_per_million_nanos=to_nanos("1.00"),
                    output_price_per_million_nanos=to_nanos("1.00"),
                )
            )
            await session.commit()
        response = client.post("/v1beta/models/primary-1:streamGenerateContent", json=_BODY)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    (row,) = rows
    # One prompt token and one completion token at 1.00 per million, in nano-units.
    assert row.cost_nanos == 2 * to_nanos("1.00") // 1_000_000
    assert row.model == "backup-1"


async def test_the_notice_a_step_owes_the_caller_still_leads_the_fallbacks_stream() -> None:
    """Two features meeting: `FRD-309`'s notice is prepended to the **answer**, and with a chain
    the answer comes from a model the notice does not mention. A caller must still be told what
    was done to their request — the notice is about the pipeline, not about the model."""
    from aira_gateway.pipeline.config import PipelineStep, StepType

    pipeline = Pipeline(
        steps=(
            PipelineStep(
                StepType.MODEL_ROUTE,
                {
                    "model": "router-1",
                    "categories": [{"name": "cheap", "model": "primary-1"}],
                    "notice": "Routed to {model} for cost.",
                },
            ),
        ),
        fallback_models=("backup-1",),
    )

    class _Router(_Server):
        async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
            return CanonicalResponse(model="router-1", text="cheap", usage=_USAGE)

    app = _app((), _Unreachable("primary-1"), _Server("backup-1"), _Router("router-1"))
    app.state.pipeline_store = _FixedStore(pipeline)
    with TestClient(app) as client:
        await _catalogue(app, "primary-1", "backup-1", "router-1")
        response = client.post("/v1beta/models/primary-1:streamGenerateContent", json=_BODY)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert "Routed to primary-1 for cost." in response.text, response.text
    assert "by:backup-1" in response.text
    # The notice names where the router *sent* it; the row names who answered. Both are true.
    assert [row.model for row in rows if row.operation == "streamGenerateContent"] == ["backup-1"]
