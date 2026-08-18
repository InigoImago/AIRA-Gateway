"""Steps in combination, in every order, with a fallback chain under them.

**Testing each step alone is not testing the pipeline.** A step is a function; a pipeline is an
ordered sequence in which each step sees what the one before it left. Almost everything interesting
lives in that seam:

- the router classifies whatever text reaches it, so a redaction *before* it means the classifier
  never reads the personal data — and after it means the classifier did;
- a step that blocks must stop the ones behind it and keep what the ones in front of it recorded;
- a rewrite has to survive routing, and both have to survive a fallback hop;
- and `run` and `dry_run` have to agree about all of it, which is the property the single dispatch
  table was built for and the one a second hand-written copy silently loses.

So the cases below are parametrised over **orders** rather than written one per step, and the last
of them compares the two execution paths across every combination.
"""

from __future__ import annotations

import itertools

import pytest

from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.dispatch import dispatch_with_fallback
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

DIRTY = "Bitte an Max Mustermann in der Hauptstrasse 3 senden."
CLEAN = "Bitte an <PERSON> in der <ADDRESS> senden."


class _Model:
    """One provider standing for one model, answering whatever it is told to.

    `seen` is the point of the class: what a step was *given* is what the seam is about, and every
    ordering case below asserts on it rather than on a step's own return value.
    """

    is_test_double = True

    def __init__(self, name: str, answer: str = "ok", *, fails: bool = False) -> None:
        self.name = name
        self._answer = answer
        self._fails = fails
        self.seen: list[str] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self.name, self.name, ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.seen.append(request.messages[-1].text)
        if self._fails:
            raise UpstreamError(503, f"{self.name} is down")
        return CanonicalResponse(
            model=self.name,
            text=self._answer,
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=2),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _filter(**config: object) -> PipelineStep:
    return PipelineStep(
        type=StepType.INJECTION_FILTER,
        config={"mode": "llm", "model": "guard", "action": "block", **config},
    )


def _pii(**config: object) -> PipelineStep:
    return PipelineStep(
        type=StepType.PII_FILTER,
        config={"model": "scrubber", "notice": "Eingabe angepasst.", **config},
    )


def _route(**config: object) -> PipelineStep:
    return PipelineStep(
        type=StepType.MODEL_ROUTE,
        config={
            "model": "judge",
            "categories": [{"name": "code", "model": "coder"}],
            "notice": "Als {category} an {model}.",
            **config,
        },
    )


STEPS = {"filter": _filter, "pii": _pii, "route": _route}


def _engine(*, verdict: str = "SAFE", rewrite: str = CLEAN, category: str = "code") -> tuple:
    guard = _Model("guard", verdict)
    scrubber = _Model("scrubber", rewrite)
    judge = _Model("judge", category)
    coder = _Model("coder", "answer from coder")
    answering = _Model("mock-1", "answer from mock-1")
    registry = ProviderRegistry([guard, scrubber, judge, coder, answering])
    return PipelineEngine(registry), {
        "guard": guard,
        "scrubber": scrubber,
        "judge": judge,
        "coder": coder,
        "mock-1": answering,
    }


def _request(text: str = DIRTY) -> CanonicalRequest:
    return CanonicalRequest(model="mock-1", messages=[CanonicalMessage(role=Role.USER, text=text)])


def _pipeline(*names: str, fallbacks: tuple[str, ...] = ()) -> Pipeline:
    return Pipeline(steps=tuple(STEPS[name]() for name in names), fallback_models=fallbacks)


# == the seam: what each step is given ===========================================================


def _wrapped(text: str) -> str:
    """The redactor's input as the redactor receives it — data between markers."""
    return f"<<<TEXT>>>\n{text}\n<<<END>>>"


def _redactor_saw(models: dict, text: str) -> bool:
    """The redactor is handed its input **as data**, between markers (`FRD-125`).

    The property these cases are about is *which* text reached the redactor, not how it was framed.
    The framing was added on 2026-08-17 after a measured hijack: a prompt that is itself an
    instruction ("Answer with the number only") came back from the redactor as `"9"` — it solved the
    riddle instead of rewriting it. Asserting equality here would tie these cases to the wrapper and
    say nothing more about the thing they exist to check.
    """
    return list(models["scrubber"].seen) == [_wrapped(text)]


async def test_redacting_before_routing_keeps_the_personal_data_from_the_classifier() -> None:
    """**The reason order is a decision and not a preference.**

    The routing classifier is a model call like any other: it reads the prompt. Put the redactor
    after it and the personal data has already been sent to a second model — one an operator chose
    for its judgement rather than for being trusted with names.
    """
    engine, models = _engine()

    await engine.run(_pipeline("pii", "route"), _request())

    assert _redactor_saw(models, DIRTY), "the redactor is given the original"
    assert models["judge"].seen == [CLEAN], "the classifier is given the redacted text"


async def test_routing_before_redacting_shows_the_classifier_the_original() -> None:
    """The other order, asserted rather than forbidden: it is a legitimate configuration — the
    classifier may be the same trusted model — and a reader has to be able to see which one they
    built. What must not happen is the two behaving identically."""
    engine, models = _engine()

    await engine.run(_pipeline("route", "pii"), _request())

    assert models["judge"].seen == [DIRTY]
    assert _redactor_saw(models, DIRTY)


async def test_a_block_stops_the_steps_behind_it_and_keeps_what_ran_in_front() -> None:
    """`FRD-122` FR-4: a blocked request records *why*, not only *that*."""
    engine, models = _engine(verdict="INJECTION")
    decisions: list[dict] = []

    with pytest.raises(PipelineRejected):
        await engine.run(_pipeline("pii", "filter", "route"), _request(), decisions=decisions)

    assert _redactor_saw(models, DIRTY), "the step in front of the block ran"
    assert models["judge"].seen == [], "the step behind it did not"
    assert [d["step"] for d in decisions] == ["pii_filter", "injection_filter"]


async def test_every_step_that_ran_reports_what_it_spent_even_when_a_later_one_blocks() -> None:
    """`FRD-125b`. A use case running a blocking pipeline over refused traffic pays for exactly
    the steps that ran, and a bill that stops at the block understates it."""
    engine, _ = _engine(verdict="INJECTION")
    calls: list = []

    with pytest.raises(PipelineRejected):
        await engine.run(_pipeline("pii", "filter"), _request(), model_calls=calls)

    assert [call.step for call in calls] == ["pii_filter", "injection_filter"]


async def test_notices_accumulate_in_the_order_the_steps_ran() -> None:
    """Two steps, two sentences, and the reader meets them in the order they happened to them."""
    engine, _ = _engine()

    outcome = await engine.run(_pipeline("pii", "route"), _request())

    assert outcome.notices == ["Eingabe angepasst.", "Als code an coder."]
    assert outcome.request.model == "coder"
    assert outcome.request.messages[-1].text == CLEAN


async def test_a_repeated_step_runs_twice() -> None:
    """Nothing forbids it, so it has to be defined. Two redactors are a chain, not a conflict."""
    engine, models = _engine(rewrite=CLEAN)

    outcome = await engine.run(_pipeline("pii", "pii"), _request())

    assert list(models["scrubber"].seen) == [_wrapped(DIRTY), _wrapped(CLEAN)], (
        "the second is given the first's output"
    )
    # The second changed nothing, so it owes no notice — one sentence, not two identical ones.
    assert outcome.notices == ["Eingabe angepasst."]


# == the chain underneath ========================================================================


async def test_a_rewritten_request_is_what_a_fallback_candidate_receives() -> None:
    """The redaction must not be undone by the primary failing.

    A fallback re-dispatches **the request**, and if the pipeline's rewrite lived anywhere other
    than in that object the second candidate would be sent the original — the failure mode being
    a use case where redaction works until an upstream has a bad minute.
    """
    engine, models = _engine()
    outcome = await engine.run(_pipeline("pii"), _request())

    broken = _Model("mock-1", fails=True)
    spare = _Model("spare", "answer from spare")
    dispatched = await dispatch_with_fallback(
        ProviderRegistry([broken, spare]), outcome.request, ("spare",)
    )

    assert dispatched.response.model == "spare"
    assert broken.seen == [CLEAN] and spare.seen == [CLEAN]


async def test_a_routed_request_still_falls_back() -> None:
    """Routing picks the *primary*; the chain is what happens when that one cannot answer."""
    engine, _ = _engine()
    outcome = await engine.run(_pipeline("route", fallbacks=("spare",)), _request())

    broken = _Model("coder", fails=True)
    spare = _Model("spare", "answer from spare")
    dispatched = await dispatch_with_fallback(
        ProviderRegistry([broken, spare]), outcome.request, outcome.fallback_models
    )

    assert outcome.request.model == "coder"
    assert dispatched.response.model == "spare"
    assert dispatched.candidate_index == 1


# == the two execution paths, over every combination =============================================


ORDERS = [
    pytest.param(order, id="+".join(order))
    for size in (1, 2, 3)
    for order in itertools.permutations(STEPS, size)
]


@pytest.mark.parametrize("order", ORDERS)
async def test_run_and_dry_run_agree_for_every_order(order: tuple[str, ...]) -> None:
    """**The property the single dispatch table exists for**, asserted across all fifteen
    combinations of one, two and three steps.

    `run` and `dry_run` used to carry a branch each. They differed — measured — about a router
    whose classifier could not be reached: one applied the default model, the other reported
    "unchanged", so the builder's preview named a model production would not use. Nothing compared
    them, so nothing failed.

    This compares them. A divergence in any order, for any step, is one test going red rather than
    a screen quietly lying about what a pipeline does.
    """
    engine, _ = _engine()
    pipeline = _pipeline(*order)

    preview = await engine.dry_run(pipeline, _request())
    outcome = await engine.run(pipeline, _request())

    assert preview.effective_model == outcome.request.model
    assert preview.blocked is False
    assert [entry.type for entry in preview.trace] == [step.type.value for step in pipeline.steps]


@pytest.mark.parametrize("order", ORDERS)
async def test_a_block_is_reported_by_both_paths_in_every_order(order: tuple[str, ...]) -> None:
    """And the same for a refusal, which is the answer somebody is most likely to be surprised by:
    the dry run has to say the request would be blocked, and by which step."""
    engine, _ = _engine(verdict="INJECTION")
    pipeline = _pipeline(*order)
    blocks = "filter" in order

    preview = await engine.dry_run(pipeline, _request())

    if not blocks:
        assert preview.blocked is False
        return
    assert preview.blocked is True
    assert preview.trace[-1].type == "injection_filter"
    with pytest.raises(PipelineRejected):
        await engine.run(pipeline, _request())
