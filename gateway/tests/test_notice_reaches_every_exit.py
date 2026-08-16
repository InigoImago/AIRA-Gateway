"""What a caller is owed about their own request being changed reaches **every** exit (`FRD-309`).

`with_notices` said so in its own docstring — *"One function, called from every exit, for the
reason `FRD-128` gives: a fact applied at each `return` is a fact eventually missing from one of
them"* — and a grep on 2026-08-15 found exactly one production caller, Gemini's
`:generateContent`. The KIRA surface's `/chat` and **both** streams applied nothing at all.

The cost is precise. A use case configures a `pii_filter` or a `model_route` notice; the builder
shows it as active; the request is rewritten or re-targeted; and three quarters of the callers are
told nothing. The audit row said nothing either, so "no notice shown" and "nothing was redacted"
were indistinguishable afterwards — which is the exact pair `notice_outcome` exists to keep apart.

Two guards, because they fail differently:

- the **behavioural** half drives all four exits and reads the answer;
- the **structural** half walks the surfaces and fails on a fifth exit that dispatches without
  applying it — a test that cannot be satisfied by remembering, which is what `FRD-126` and
  `test_every_dispatch_applies_the_conditions.py` both settled for the same shape of hole.
"""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator
from pathlib import Path
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
from aira_gateway.db.models import ModelRead
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

NOTICE = "Hinweis: als code eingestuft."
ANSWER = "Die Antwort steht hier."
MODEL = "m1"
NUMERIC_ID = 4711

_GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "Schreib mir eine Funktion."}]}]}
_KIRA_BODY = {
    "request": {"parts": [{"text": "Schreib mir eine Funktion."}]},
    "model_id": NUMERIC_ID,
}


class _Model:
    """One model that answers the router's question and then the caller's.

    Told apart by what the request **is** rather than by how wide its allowance happens to be —
    the lesson `test_pipeline_accounting._Guard` records, where a fixture keyed on the token cap
    started reading the classifier's call as the caller's.
    """

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(MODEL, MODEL, ("generateContent",), provider="test", region="eu")]

    @staticmethod
    def _routing(request: CanonicalRequest) -> bool:
        return any("routing classifier" in (message.text or "") for message in request.messages)

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=MODEL,
            text="code" if self._routing(request) else ANSWER,
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=2),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        for piece in ANSWER.split(" "):
            yield CanonicalChunk(text_delta=piece + " ")
        yield CanonicalChunk(
            text_delta="",
            finish_reason="stop",
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=4),
        )

    async def embed(self, request: object) -> list[list[float]]:  # pragma: no cover - unused here
        return [[0.0]]


class _Store:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: Any) -> Pipeline:
        return self._pipeline


def _routing_pipeline() -> Pipeline:
    """A router that matches a category and says so. It re-targets nothing — the notice is owed
    for the **classification**, not for a change of model, which is why one model is enough."""
    return Pipeline(
        steps=(
            PipelineStep(
                type=StepType.MODEL_ROUTE,
                config={
                    "model": MODEL,
                    "categories": [{"name": "code", "description": "Programmieraufgaben"}],
                    "notice": "Hinweis: als {category} eingestuft.",
                },
            ),
        ),
        fallback_models=(),
    )


def _app():  # noqa: ANN201
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    registry = ProviderRegistry([_Model()])
    app.state.providers = registry
    # The engine holds its own reference from app construction; replacing only `providers` leaves
    # it resolving against the original registry (`test_pipeline_accounting` records the same trap).
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store(_routing_pipeline())
    return app


async def _catalogue(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=MODEL, numeric_id=NUMERIC_ID, capabilities=["generate"]))
        await session.commit()


def _events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _sse_text(body: str) -> str:
    """Every ``update`` event's text, concatenated — what a streaming client actually renders."""
    return "".join(str(event["data"]) for event in _events(body) if event.get("status") == "update")


# == the four exits ==============================================================================


async def test_the_gemini_answer_carries_the_notice() -> None:
    """The one exit that always had it, kept here so the four are read together."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"/v1beta/models/{MODEL}:generateContent", json=_GEMINI_BODY)

    assert response.status_code == 200, response.text
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    assert text.startswith(NOTICE)
    assert text.endswith(ANSWER)


async def test_the_gemini_stream_is_led_by_the_notice() -> None:
    """A stream has no finished answer to prefix, so the notice leads it — which is also the
    reading order somebody wants: told their prompt changed *before* they read the result."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse", json=_GEMINI_BODY
        )

    assert response.status_code == 200, response.text
    streamed = "".join(
        json.loads(line.removeprefix("data: "))["candidates"][0]["content"]["parts"][0].get(
            "text", ""
        )
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    assert streamed.startswith(NOTICE)
    assert streamed.rstrip().endswith(ANSWER.rstrip())
    # Once. Every chunk repeating it would be worse than none.
    assert streamed.count(NOTICE) == 1


async def test_the_kira_answer_carries_the_notice() -> None:
    """The compatibility surface applied nothing at all — on the surface whose whole premise is
    that a migrating client changes a URL and gets the same governance."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post("/kira/api/external/chat", json=_KIRA_BODY)

    assert response.status_code == 200, response.text
    text = response.json()["parts"][0]["text"]
    assert text.startswith(NOTICE)
    assert text.endswith(ANSWER)


async def test_the_kira_stream_is_led_by_the_notice_and_its_terminal_event_agrees() -> None:
    """The ``completed`` event carries the whole answer, so it has to carry the notice too —
    a conservative predecessor client reads only that one."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post("/kira/api/external/streaming-chat", json=_KIRA_BODY)

    assert response.status_code == 200, response.text
    streamed = _sse_text(response.text)
    assert streamed.startswith(NOTICE)
    assert streamed.count(NOTICE) == 1

    completed = _events(response.text)[-1]
    assert completed["status"] == "completed"
    # What was streamed and what the terminal event reports are the same answer, or a client that
    # reads one of the two is told something the other contradicts.
    assert completed["data"]["parts"][0]["text"] == streamed


# == the structural guard ========================================================================


SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "api"

#: How an exit discharges `FRD-309`. `annotate` for a finished answer, `StreamedNotice` for one
#: that is still arriving — see `serving.py` for why a stream cannot use the first.
APPLIES = frozenset({"annotate", "StreamedNotice"})

#: How a caller's own generation is dispatched. The pipeline's classifier and redactor call
#: `generate` too, and they are not exits: they are the gateway acting *on* a request rather than
#: serving it — which is why this looks for the two the **surfaces** use.
SERVES = frozenset({"dispatch_with_fallback", "stream_generate"})

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _chains(path: Path) -> list[list[Function]]:
    """Every function in the file with the chain of functions enclosing it.

    The chain is load-bearing, exactly as it is in
    `test_every_dispatch_applies_the_conditions._scopes`: a stream dispatches inside a closure
    while the notice is constructed one scope out, and a walk that stopped at the innermost
    function would report the hole's own location as clean.
    """
    found: list[list[Function]] = []

    def visit(node: ast.AST, enclosing: list[Function]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, Function):
                chain = [child, *enclosing]
                found.append(chain)
                visit(child, chain)
            else:
                visit(child, enclosing)

    visit(ast.parse(path.read_text()), [])
    return found


def _names_called(function: Function) -> set[str]:
    """Names this function calls itself, not the ones its nested functions call."""
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, Function):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name | ast.Attribute):
                names.add(child.func.id if isinstance(child.func, ast.Name) else child.func.attr)
            visit(child)

    visit(function)
    return names


def test_every_surface_that_serves_a_generation_applies_the_notice() -> None:
    """A fifth exit cannot be written without answering this.

    Behaviour tests cover the four that exist; this covers the one somebody adds next year. It is
    the same instrument `FRD-126` reached for after the KIRA surface lost its rate limiting — the
    guarantee is about *every* exit, so the guard has to enumerate them rather than list the ones
    that were known to be broken.
    """
    unguarded: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for chain in _chains(path):
            if not SERVES & _names_called(chain[0]):
                continue
            applied = set().union(*(_names_called(scope) for scope in chain))
            if not APPLIES & applied:
                where = "/".join(path.relative_to(SOURCE).parts)
                unguarded.append(f"{where}:{'.'.join(f.name for f in reversed(chain))}")

    assert not unguarded, (
        "these serve a caller's generation and never apply FRD-309's notice, so a use case whose "
        "pipeline rewrites or re-targets a request tells that caller nothing:\n  "
        + "\n  ".join(unguarded)
        + "\n\nUse `serving.annotate` for a finished answer or `serving.StreamedNotice` for a "
        "stream. Both also record what became of the notice, which is the half a reader needs "
        "when it could not be shown."
    )
