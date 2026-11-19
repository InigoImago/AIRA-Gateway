"""A batch of texts fails as one unit, and keeps what it spent (`FRD-309` FR-10, `FRD-125`).

An embedding's texts are redacted a handful at a time, each its own model call. When one of them
fails in a way no step decides on — a fault in this gateway — the request has failed, and two
things must hold. The redactions still running are stopped: their answers will never be sent, and
each one left running is a call paid for nothing. And the texts that **did** finish are on the
trail, audited and billed, because their calls were made and their tokens spent whatever happened
to the request after. A batch that raised the first failure and let the rest run did neither.
"""

from __future__ import annotations

import asyncio

import pytest

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

#: Two texts that finish at once, one that fails once they have, two that wait to be let finish.
TEXTS = ["quick one", "quick two", "faulty", "patient three", "patient four"]


class _Redactor:
    """Redacts every text but ``faulty``, which raises a fault in this gateway once both quick
    texts are done. The patient texts finish only when the test lets them — so whether they were
    stopped or are still running is a fact the test can read, not a race it has to win."""

    def __init__(self) -> None:
        self.finished = 0
        self.quick_done = asyncio.Event()
        self.let_finish = asyncio.Event()

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("trusted", "trusted", ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        text = request.messages[-1].text.split("\n", 1)[1].rsplit("\n", 1)[0]
        if text == "faulty":
            await self.quick_done.wait()
            raise RuntimeError("a fault in this gateway")
        if text.startswith("patient"):
            await self.let_finish.wait()
        self.finished += 1
        if self.finished == 2:
            self.quick_done.set()
        return CanonicalResponse(
            model="trusted", text=text, usage=CanonicalUsage(prompt_tokens=40, completion_tokens=4)
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _redaction() -> Pipeline:
    return Pipeline(steps=(PipelineStep(StepType.PII_FILTER, {"model": "trusted"}),))


async def _run_until_it_fails(redactor: _Redactor, calls: list[ModelCall]) -> None:
    engine = PipelineEngine(ProviderRegistry([redactor]))
    # The fault itself, not a group around it: the surfaces handle what one text raises.
    with pytest.raises(RuntimeError, match="a fault in this gateway"):
        await engine.run_over_texts(_redaction(), TEXTS, model="embedder", model_calls=calls)
    # Whatever is still running may finish now, and is given the turns to do it.
    redactor.let_finish.set()
    for _ in range(10):
        await asyncio.sleep(0)


async def test_a_failure_stops_the_redactions_still_running() -> None:
    redactor = _Redactor()

    await _run_until_it_fails(redactor, [])

    assert redactor.finished == 2, "redactions went on after the request had failed"


async def test_what_the_finished_texts_cost_is_on_the_trail() -> None:
    redactor = _Redactor()
    calls: list[ModelCall] = []

    await _run_until_it_fails(redactor, calls)

    # One row for the step, as a batch always leaves (`FRD-309` FR-11), carrying both quick texts.
    assert [(call.step, call.model) for call in calls] == [("pii_filter", "trusted")]
    assert (calls[0].usage.prompt_tokens, calls[0].usage.completion_tokens) == (80, 8)
