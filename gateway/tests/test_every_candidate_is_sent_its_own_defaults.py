"""A chain sends each candidate the caller's request with **that candidate's** defaults.

`prepare_for_dispatch` fits the request to the model routing chose: the output cap it declares when
the caller set none (`FRD-114` FR-2), the thinking it declares as its default (`FRD-111`), the cache
marker it can honour (`FRD-133`). The chain then changed a candidate's name and addressing and
nothing else, so a fallback was sent the primary's settings as if the caller had asked for them:

- a primary declaring a default cap of 5 tokens cut the fallback's answer at 5 — `MAX_TOKENS` with
  a 200, or a `502` for a structured answer after the vendor had been paid;
- a primary's default thinking became a condition the fallback could not meet, and a request that
  asked for no thinking was refused for it;
- a cache marker went to a model that does not declare prompt caching.

And the cap a caller *did* ask for was checked against the model routing chose and never against a
fallback, so a fallback declaring a lower maximum was called anyway: refused by its vendor after
the call, or answering with less than was asked for.

Every case is asked on both surfaces and on both forms of the chain. The fit is one function, and
asking it four ways is what keeps it one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

PRIMARY, FALLBACK = "primary-1", "fallback-1"
SLUG = "fitting"
_USAGE = CanonicalUsage(prompt_tokens=1, completion_tokens=1)

#: Each way into the chain, as `(path, body)` with room for the case's own settings.
SURFACES: dict[str, str] = {
    "gemini generate": f"/v1beta/models/{PRIMARY}:generateContent",
    "gemini stream": f"/v1beta/models/{PRIMARY}:streamGenerateContent",
    "kira chat": "/kira/api/external/chat",
    "kira streaming-chat": "/kira/api/external/streaming-chat",
}


def _body(surface: str, *, max_tokens: int | None = None, thinking: dict | None = None) -> dict:
    """The same request in each surface's own words."""
    if surface.startswith("kira"):
        body: dict[str, Any] = {"request": {"parts": [{"text": "hello"}]}, "model_id": 9001}
        if max_tokens is not None:
            body["maxTokens"] = max_tokens
        if thinking is not None:
            body["thinking"] = thinking
        return body
    config: dict[str, Any] = {}
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens
    if thinking is not None:
        config["thinkingConfig"] = {"thinkingBudget": thinking["tokens"]}
    body = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
    if config:
        body["generationConfig"] = config
    return body


class _Recording:
    """A model that records the request it was sent, on both verbs, and fails if told to."""

    is_test_double = True

    def __init__(self, model: str, *, fails: bool = False) -> None:
        self.name = model
        self._model = model
        self._fails = fails
        self.sent: list[CanonicalRequest] = []

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent")
        return [UpstreamModel(self._model, self._model, verbs, "acme")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.sent.append(request)
        if self._fails:
            raise UpstreamError(f"{self._model} is unavailable", 503)
        return CanonicalResponse(model=self._model, text=f"by:{self._model}", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        self.sent.append(request)
        if self._fails:
            raise UpstreamError(f"{self._model} is unavailable", 503)
        yield CanonicalChunk(text_delta=f"by:{self._model}", usage=_USAGE, finish_reason="stop")

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Chain:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(*models: _Recording) -> Any:
    """The first model is the one requests name; the others are its chain, in order."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry(list(models))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    chain = tuple(model.name for model in models[1:])
    app.state.pipeline_store = _Chain(Pipeline(fallback_models=chain))
    return app


async def _setup(app: Any, *declared: dict[str, Any], **use_case: Any) -> None:
    """Each model as the catalogue declares it, in the order `_app` was given them, and the use
    case the requests name."""
    names = [model.name for model in app.state.providers.each()]
    async with app.state.db_sessionmaker() as session:
        for number, (model, declaration) in enumerate(zip(names, declared, strict=True)):
            session.add(
                ModelRead(
                    model=model,
                    numeric_id=9001 + number,
                    approved=True,
                    **{"capabilities": ["generate"], **declaration},
                )
            )
        session.add(UseCaseRead(slug=SLUG, name=SLUG, **use_case))
        await session.commit()


def _send(client: TestClient, surface: str, body: dict) -> Any:
    return client.post(SURFACES[surface], json=body, headers={"X-AIRA-Use-Case": SLUG})


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


# == the output cap ==============================================================================


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_a_fallback_is_not_held_to_the_primarys_default_cap(surface: str) -> None:
    """The case found in the settings combinations: the primary's default of 5 tokens cut the
    fallback's answer at 5. The caller asked for no cap, the fallback declares none, so none is
    sent — the vendor's own default applies, as it would had the fallback been named."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    with TestClient(app) as client:
        await _setup(app, {"default_max_output_tokens": 5}, {})
        response = _send(client, surface, _body(surface))

    assert response.status_code == 200, response.text
    assert primary.sent[0].max_output_tokens == 5, "the primary is still sent its own default"
    assert fallback.sent[0].max_output_tokens is None


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_a_fallback_is_sent_its_own_default_cap(surface: str) -> None:
    """The other direction: a fallback that declares a default of its own (`FRD-119`, Anthropic
    needs one) is sent that — not the primary's, and not nothing."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    with TestClient(app) as client:
        await _setup(app, {"default_max_output_tokens": 5}, {"default_max_output_tokens": 77})
        response = _send(client, surface, _body(surface))

    assert response.status_code == 200, response.text
    assert fallback.sent[0].max_output_tokens == 77


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_the_callers_own_cap_reaches_every_candidate(surface: str) -> None:
    """What the caller asked for is theirs on every hop; a default fills only what they left."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    with TestClient(app) as client:
        await _setup(app, {"default_max_output_tokens": 5}, {"default_max_output_tokens": 77})
        response = _send(client, surface, _body(surface, max_tokens=300))

    assert response.status_code == 200, response.text
    assert [request.max_output_tokens for request in (*primary.sent, *fallback.sent)] == [300, 300]


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_a_fallback_that_cannot_give_the_cap_asked_for_is_passed_over(surface: str) -> None:
    """A cap the caller set is a condition per hop, as it is for the model routing chose (where it
    is a `400` before dispatch). A fallback declaring a lower maximum is skipped by name rather than
    called — its vendor would refuse it after the call, or answer with less than was asked for —
    and the next one that can give it serves."""
    primary, short, long = (
        _Recording(PRIMARY, fails=True),
        _Recording(FALLBACK),
        _Recording("fallback-2"),
    )
    app = _app(primary, short, long)
    with TestClient(app) as client:
        await _setup(app, {"max_output_tokens": 1000}, {"max_output_tokens": 64}, {})
        response = _send(client, surface, _body(surface, max_tokens=500))
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert short.sent == [], "a fallback that cannot give the cap asked for was called"
    assert [request.max_output_tokens for request in long.sent] == [500]
    (row,) = rows
    assert {
        "step": "dispatch",
        "action": "skipped",
        "to": FALLBACK,
        "why": "accepts at most 64 output tokens, and this request asks for 500",
    } in (row.pipeline_decisions or []), row.pipeline_decisions


# == thinking ====================================================================================


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_a_primarys_default_thinking_is_not_a_condition_on_its_fallback(surface: str) -> None:
    """The caller asked for no thinking. The primary thinks by default; the fallback declares no
    thinking at all — so it is sent none, and serves, rather than being skipped for failing to
    honour a setting nobody asked for."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    thinks = {
        "capabilities": ["generate", "thinking"],
        "thinking": {
            "modes": ["limited", "disabled"],
            "min_tokens": 1,
            "max_tokens": 4096,
            "default": {"mode": "limited", "tokens": 256},
        },
    }
    with TestClient(app) as client:
        await _setup(app, thinks, {})
        response = _send(client, surface, _body(surface))

    assert response.status_code == 200, response.text
    assert primary.sent[0].thinking is not None, "the primary is still sent its own default"
    assert fallback.sent[0].thinking is None


async def test_thinking_the_caller_asked_for_is_still_a_condition_on_every_hop() -> None:
    """The half that must not have been loosened: a request that *asked* to think is not served by
    a fallback that cannot, which would be a worse answer with a 200 (`ThinkingHonoured`)."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    thinks = {
        "capabilities": ["generate", "thinking"],
        "thinking": {"modes": ["limited", "disabled"], "min_tokens": 1, "max_tokens": 4096},
    }
    with TestClient(app) as client:
        await _setup(app, thinks, {})
        response = _send(
            client, "gemini generate", _body("gemini generate", thinking={"tokens": 256})
        )

    assert response.status_code == 503, response.text
    assert fallback.sent == []


@pytest.mark.parametrize("surface", ["gemini generate", "gemini stream"])
async def test_switching_thinking_off_is_resolved_against_each_candidate(surface: str) -> None:
    """`FRD-111` §11's open item, closed by fitting per hop. "Do not think" asked of a model that
    cannot think is already true, so nothing is sent — and that has to be decided against the
    candidate being tried: carried from the primary, the explicit off reached a fallback with no
    such field, and its vendor refused the request."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    thinks = {
        "capabilities": ["generate", "thinking"],
        "thinking": {"modes": ["limited", "disabled"], "min_tokens": 1, "max_tokens": 4096},
    }
    with TestClient(app) as client:
        await _setup(app, thinks, {})
        response = _send(client, surface, _body(surface, thinking={"tokens": 0}))

    assert response.status_code == 200, response.text
    assert primary.sent[0].thinking is not None, "the primary is told to switch it off"
    assert fallback.sent[0].thinking is None, "a model without thinking is sent no setting"


async def test_a_thinking_budget_that_does_not_fit_a_fallbacks_cap_passes_it_over() -> None:
    """A budget is drawn from the output allowance, and the allowance is per candidate now: it
    fits the primary's default of 1000 and not the first fallback's of 64. Asked of the model
    routing chose that is the caller's `400`; asked of a fallback, it is a reason to pass over it,
    and the next candidate it fits serves."""
    primary, short, long = (
        _Recording(PRIMARY, fails=True),
        _Recording(FALLBACK),
        _Recording("fallback-2"),
    )
    app = _app(primary, short, long)
    thinks = {
        "capabilities": ["generate", "thinking"],
        "thinking": {"modes": ["limited", "disabled"], "min_tokens": 1, "max_tokens": 4096},
    }
    with TestClient(app) as client:
        await _setup(
            app,
            {**thinks, "default_max_output_tokens": 1000},
            {**thinks, "default_max_output_tokens": 64},
            {**thinks, "default_max_output_tokens": 2000},
        )
        response = _send(
            client, "gemini generate", _body("gemini generate", thinking={"tokens": 256})
        )
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    assert short.sent == []
    assert [(request.max_output_tokens, request.thinking.tokens) for request in long.sent] == [
        (2000, 256)
    ]
    skipped = [d for d in rows[0].pipeline_decisions or [] if d.get("to") == FALLBACK]
    assert skipped and "64-token output allowance" in skipped[0]["why"], skipped


# == the cache marker ============================================================================


@pytest.mark.parametrize("surface", sorted(SURFACES))
async def test_a_cache_marker_goes_only_to_a_model_that_declares_caching(surface: str) -> None:
    """Whether a request carries a cache marker depends on the model it lands on (`FRD-133`), and
    the use case's own switch — so the primary is sent one and the fallback, which declares no
    prompt caching, is not."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    with TestClient(app) as client:
        await _setup(
            app,
            {"capabilities": ["generate", "prompt_caching"]},
            {},
            prompt_caching_enabled=True,
        )
        response = _send(client, surface, _body(surface))

    assert response.status_code == 200, response.text
    assert primary.sent[0].cache_prefix is True
    assert fallback.sent[0].cache_prefix is False


async def test_a_fallback_that_declares_caching_is_sent_the_marker() -> None:
    """And the other way round: the marker is the fallback's own decision, not merely withheld."""
    primary, fallback = _Recording(PRIMARY, fails=True), _Recording(FALLBACK)
    app = _app(primary, fallback)
    with TestClient(app) as client:
        await _setup(
            app,
            {},
            {"capabilities": ["generate", "prompt_caching"]},
            prompt_caching_enabled=True,
            prompt_cache_ttl="1h",
        )
        response = _send(client, "gemini generate", _body("gemini generate"))

    assert response.status_code == 200, response.text
    assert primary.sent[0].cache_prefix is False
    assert (fallback.sent[0].cache_prefix, fallback.sent[0].cache_ttl) == (True, "1h")
