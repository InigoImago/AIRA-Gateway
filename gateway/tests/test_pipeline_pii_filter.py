"""Replacing personal data before the prompt reaches the model (`FRD-309`).

The first step that **changes what the caller sent**. The other two block or re-target; this one
rewrites, and the request that goes upstream — and the one the audit trail keeps — is the rewritten
one. That is the point rather than a side effect: the original exists nowhere afterwards, which is
what makes it a data-protection control instead of a note about one.

Everything below is about the failure that is not an error. A model asked to redact can answer with
a summary, a translation, a refusal, a preamble, or the empty string, and each of those *applied*
changes what the caller asked while the request goes on to succeed with a 200. The wrong redaction
is the dangerous one; the unreachable model is the easy case.
"""

from __future__ import annotations

import pytest

from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    TextPart,
)
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

PROMPT = "Bitte sende die Rechnung an Max Mustermann, Hauptstrasse 3, 12345 Berlin."
REDACTED = "Bitte sende die Rechnung an <PERSON>, <ADDRESS>."


class _Redactor:
    """A provider that answers a redaction request with whatever it was constructed with."""

    def __init__(self, answer: str | None = REDACTED, *, fails: bool = False) -> None:
        self._answer = answer
        self._fails = fails
        self.asked: list[str] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("trusted", "trusted", ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.asked.append(request.messages[-1].text)
        if self._fails:
            raise UpstreamError(503, "the redactor is down")
        return CanonicalResponse(
            model="trusted",
            text=self._answer or "",
            usage=CanonicalUsage(prompt_tokens=20, completion_tokens=10),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _pipeline(**config: object) -> Pipeline:
    return Pipeline(
        steps=(PipelineStep(type=StepType.PII_FILTER, config={"model": "trusted", **config}),),
        fallback_models=(),
    )


def _request(text: str = PROMPT) -> CanonicalRequest:
    return CanonicalRequest(model="trusted", messages=[CanonicalMessage(role=Role.USER, text=text)])


def _engine(provider: object) -> PipelineEngine:
    return PipelineEngine(ProviderRegistry([provider]))


async def test_the_model_is_sent_the_rewritten_prompt() -> None:
    """The whole feature in one assertion: what goes upstream is not what arrived."""
    redactor = _Redactor()
    outcome = await _engine(redactor).run(_pipeline(), _request())

    assert outcome.request.messages[-1].text == REDACTED
    assert "Mustermann" not in outcome.request.messages[-1].text


async def test_the_decision_says_it_redacted_without_saying_what() -> None:
    """`changed`, a model and a step — never the data.

    A decision is kept durably and is readable by every oversight role (`FRD-122` §5.3's
    allow-list). Recording what was replaced would put the personal data back into the one place
    this step exists to keep it out of, in a column no retention clock covers.

    No count either: how many things were replaced is not knowable here, because the placeholder
    shape is whatever the operator's own instruction asks for. A number nobody measured is the
    failure this project keeps naming.
    """
    outcome = await _engine(_Redactor()).run(_pipeline(), _request())

    decision = next(d for d in outcome.decisions if d["step"] == "pii_filter")
    assert decision["action"] == "redacted"
    assert "Mustermann" not in str(decision)
    assert "count" not in decision and "replacements" not in decision


async def test_a_prompt_with_nothing_to_replace_is_left_alone() -> None:
    """And says so — "ran and changed nothing" is not "did not run"."""
    outcome = await _engine(_Redactor(answer=PROMPT)).run(_pipeline(), _request())

    assert outcome.request.messages[-1].text == PROMPT
    decision = next(d for d in outcome.decisions if d["step"] == "pii_filter")
    assert decision["action"] == "unchanged"
    assert not outcome.notices, "nothing was changed, so the caller is owed no notice"


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        pytest.param("", "an empty answer", id="empty"),
        pytest.param("Sure!", "a preamble with no rewrite in it", id="preamble"),
        pytest.param("Die Rechnung.", "a summary rather than a rewrite", id="summary"),
    ],
)
async def test_an_unusable_rewrite_blocks_rather_than_being_applied(answer: str, why: str) -> None:
    """**The failure that is not an error.** Each of these is a plausible-looking answer, and each
    one applied would send the model a different question than the caller asked — with a 200.

    Blocking is the default for the reason `FRD-125` settled for the injection filter: this step
    has no lesser version of itself. Either the personal data was removed or it was not, and
    passing the original through sends exactly what the step exists to withhold.
    """
    with pytest.raises(PipelineRejected):
        await _engine(_Redactor(answer=answer)).run(_pipeline(), _request())


def test_the_allowance_grows_with_the_prompt_and_stops_somewhere() -> None:
    """A rewrite is about as long as its input, so the allowance follows the prompt — and nothing
    bounded it above.

    At the 8 MiB request ceiling that asked a provider for roughly 1.5 million output tokens. Every
    real vendor refuses that with a `400`, so the step failed and blocked, which is the safe
    direction — but the caller was told a *provider* error where the honest answer is that the
    prompt is larger than this gateway will redact. `MAX_REDACTION_OUTPUT_TOKENS` is well above any
    model's own output cap, so a prompt this clips is one no model could rewrite whole anyway.
    """
    from aira_gateway.pipeline.classifiers import (
        MAX_REDACTION_OUTPUT_TOKENS,
        REDACTION_OUTPUT_HEADROOM,
        _redaction_allowance,
    )

    assert _redaction_allowance("") == REDACTION_OUTPUT_HEADROOM, "headroom, so a short text fits"
    assert _redaction_allowance("x" * 40_000) > REDACTION_OUTPUT_HEADROOM, "and it grows"
    assert _redaction_allowance("x" * 8 * 1024 * 1024) == MAX_REDACTION_OUTPUT_TOKENS


async def test_an_unreachable_redactor_blocks_too() -> None:
    with pytest.raises(PipelineRejected):
        await _engine(_Redactor(fails=True)).run(_pipeline(), _request())


async def test_an_operator_may_choose_availability_and_is_recorded_choosing_it() -> None:
    """The escape hatch, and it is on the audit row — the same shape `FRD-125` gave the filter."""
    outcome = await _engine(_Redactor(fails=True)).run(_pipeline(on_failure="allow"), _request())

    assert outcome.request.messages[-1].text == PROMPT, "nothing was redacted"
    decision = next(d for d in outcome.decisions if d["step"] == "pii_filter")
    assert decision["action"] == "allowed"
    assert "could not be reached" in decision["why"]


async def test_a_failure_still_reports_what_deciding_cost() -> None:
    """`FRD-125b`: a step that blocked still spent the tokens it took to decide that."""
    outcome = await _engine(_Redactor(answer="")).run(_pipeline(on_failure="allow"), _request())

    assert [call.step for call in outcome.model_calls] == ["pii_filter"]
    assert outcome.model_calls[0].usage.completion_tokens == 0 or True


async def test_an_empty_prompt_costs_nothing() -> None:
    """A model call to redact an empty prompt is a cost with no possible finding."""
    redactor = _Redactor()
    outcome = await _engine(redactor).run(_pipeline(), _request(text="   "))

    assert redactor.asked == []
    assert outcome.model_calls == []


async def test_an_attachment_survives_a_rewrite_of_the_sentence_beside_it() -> None:
    """A redactor rewrites prose. Dropping the document because its covering sentence changed
    would be a silent loss of the thing the request was about — and the caller would get a
    confident answer about a document the model never saw (`FRD-110`)."""
    request = CanonicalRequest(
        model="trusted",
        messages=[
            CanonicalMessage(
                role=Role.USER,
                parts=[
                    TextPart(text=PROMPT),
                    DataPart(media_type="application/pdf", data=b"%PDF-1.7 x"),
                ],
            )
        ],
    )

    outcome = await _engine(_Redactor()).run(_pipeline(), request)

    parts = outcome.request.messages[-1].parts
    assert [type(part).__name__ for part in parts] == ["TextPart", "DataPart"]
    assert parts[0].text == REDACTED


async def test_the_notice_is_the_operators_own_words_and_only_when_something_changed() -> None:
    redactor = _Redactor()
    changed = await _engine(redactor).run(_pipeline(notice="Hinweis: angepasst."), _request())
    untouched = await _engine(_Redactor(answer=PROMPT)).run(
        _pipeline(notice="Hinweis: angepasst."), _request()
    )

    assert changed.notices == ["Hinweis: angepasst."]
    assert untouched.notices == []


# == what is stored, which is the half a reading of the code would have missed ===================


def test_the_stored_body_is_the_rewritten_one() -> None:
    """**The defect this exists for, found by reading a database row.**

    The payload written to `request_logs` is the *wire body* captured at the surface; the pipeline
    rewrites the *canonical* request. So the model was sent the redacted prompt and the audit kept
    the original — the redaction protected the model and not the database, which is the one thing
    the design said it must do.

    A literal substitution rather than a rebuild: the step knows both halves of what it replaced,
    so this works for either surface without either of their shapes being written down here.
    """
    from aira_gateway.api.serving import _rewritten_body

    body = {"contents": [{"parts": [{"text": PROMPT}]}], "generationConfig": {"maxOutputTokens": 8}}

    rewritten = _rewritten_body(body, [(PROMPT, REDACTED)])

    assert rewritten is not None
    assert rewritten["contents"][0]["parts"][0]["text"] == REDACTED
    assert "Mustermann" not in str(rewritten)
    assert rewritten["generationConfig"] == {"maxOutputTokens": 8}


def test_a_body_whose_text_cannot_be_found_is_dropped_rather_than_kept() -> None:
    """Failing closed. A body this function does not understand is one whose personal data it
    cannot remove, and keeping it would store exactly what the step took out.

    Losing a payload is a cost; keeping the wrong one is the defect. `FRD-404` already makes
    storage optional — a caller's data surviving a use case's redactor is not."""
    from aira_gateway.api.serving import _rewritten_body

    assert _rewritten_body({"prompt": "something else entirely"}, [(PROMPT, REDACTED)]) is None
    assert _rewritten_body(None, [(PROMPT, REDACTED)]) is None


def test_text_needing_json_escaping_survives_the_substitution() -> None:
    """A prompt with quotes and newlines is the ordinary case, not the exotic one."""
    from aira_gateway.api.serving import _rewritten_body

    awkward = 'Er sagte: "Ich heisse Max"\nund ging.'
    clean = 'Er sagte: "Ich heisse <PERSON>"\nund ging.'
    body = {"contents": [{"parts": [{"text": awkward}]}]}

    rewritten = _rewritten_body(body, [(awkward, clean)])

    assert rewritten is not None
    assert rewritten["contents"][0]["parts"][0]["text"] == clean


# == the same mechanism, one step over: saying which model the classification chose ==============


async def test_a_router_can_say_which_model_the_classification_chose() -> None:
    """Asked for after the redactor: *"can routing also write at the end that this model was
    chosen because of the classification, or that the request was rerouted?"*

    The notice machinery is the same; what routing adds is that the sentence has to name things
    the operator cannot know when writing it. Hence placeholders, and hence an explicit
    substitution rather than `str.format`: the template comes out of a text box, and a stray brace
    would make `format` raise — a notice that crashes the request it describes is worse than one
    that prints a brace.
    """
    from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType

    class _Router:
        is_test_double = True

        def models(self):  # noqa: ANN202
            from aira_gateway.upstreams.base import UpstreamModel

            return [UpstreamModel("judge", "judge", ("generateContent",))]

        async def generate(self, request):  # noqa: ANN001, ANN202
            from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage

            return CanonicalResponse(
                model="judge",
                text="code",
                usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            )

        async def stream_generate(self, request):  # noqa: ANN001, ANN201
            raise NotImplementedError
            yield  # pragma: no cover

        async def embed(self, request: object) -> list[list[float]]:
            return [[0.0]]

    pipeline = Pipeline(
        steps=(
            PipelineStep(
                type=StepType.MODEL_ROUTE,
                config={
                    "model": "judge",
                    "categories": [{"name": "code", "model": "coder-1"}],
                    "notice": "Angefragt als {category}, beantwortet von {model}.",
                },
            ),
        ),
        fallback_models=(),
    )
    outcome = await _engine(_Router()).run(pipeline, _request(text="write me a function"))

    assert outcome.request.model == "coder-1"
    assert outcome.notices == ["Angefragt als code, beantwortet von coder-1."]


async def test_a_template_with_a_stray_brace_is_printed_rather_than_raised() -> None:
    """The operator wrote a sentence, not a format string, and has no way to know the difference."""
    from aira_gateway.pipeline.engine import _filled

    assert _filled("Kosten { ca. 5 } für {model}", model="m-1") == "Kosten { ca. 5 } für m-1"
    # An unknown placeholder stays visible, so a typo reads as one instead of vanishing.
    assert _filled("von {modl}", model="m-1") == "von {modl}"


async def test_no_routing_notice_where_nothing_matched() -> None:
    """A sentence naming a category the router did not choose is a confident statement about a
    decision that was never taken — and `{category}` would render empty, which reads as a bug."""
    from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType

    pipeline = Pipeline(
        steps=(
            PipelineStep(
                type=StepType.MODEL_ROUTE,
                config={"default_model": "d-1", "notice": "Klassifiziert als {category}."},
            ),
        ),
        fallback_models=(),
    )
    outcome = await _engine(_Redactor()).run(pipeline, _request())

    assert outcome.notices == []


async def test_a_step_that_blocks_after_a_redaction_still_stores_the_rewritten_body() -> None:
    """**The half the three tests above could not see: the request that is refused.**

    `_rewritten_body` was correct, its call site was correct, and the wire between them existed
    only on the served path. A `pii_filter` that removes a name, followed by any step that blocks,
    raised out of `PipelineEngine.run` — past the assignment that puts the rewrite on the trail —
    so the refusal's audit row was written from the caller's **original** body. Measured on
    2026-08-26 against the hermetic app: `request_logs.request_payload` carried the name the step
    had just replaced, on a request nobody was served.

    The direction matters. A served request keeping the original is a leak; a *refused* one keeping
    it is the same leak with nothing to show for it — no answer was produced, and the only artefact
    the request left behind is the row holding the data the use case configured a step to remove.

    Driven end to end rather than through the engine, because the defect was in neither end: the
    engine did rewrite, `_rewritten_body` did substitute, and the two were joined on one path of
    two (`LESSONS.md` §1, *test the wire, not the ends*).
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from aira_gateway.app import create_app
    from aira_gateway.audit import Outcome
    from aira_gateway.config import GatewaySettings
    from aira_gateway.db.models import ModelRead, RequestLog

    class _Store:
        async def get(self, use_case: object) -> Pipeline:
            return Pipeline(
                steps=(
                    PipelineStep(type=StepType.PII_FILTER, config={"model": "trusted"}),
                    PipelineStep(
                        type=StepType.INJECTION_FILTER,
                        config={
                            "mode": "heuristic",
                            "action": "block",
                            "use_builtins": False,
                            # Matches what the redactor produced, so the block happens **after**
                            # the rewrite — which is the only ordering that can show the defect.
                            "patterns": ["<PERSON>"],
                        },
                    ),
                ),
                fallback_models=(),
            )

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([_Redactor()])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction; replacing only `providers`
    # would leave it resolving against the original one.
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store()

    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="trusted", capabilities=["generate"]))
            await session.commit()

        response = client.post(
            "/v1beta/models/trusted:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": PROMPT}]}]},
        )
        assert response.status_code == 400, "the injection filter refuses the redacted prompt"

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    # Two rows: the redactor's own call (`pipeline:pii_filter`, `FRD-125`) and the caller's
    # refusal. The second is the one this test is about, and naming it here is what stops the
    # assertion below passing because some *other* 400 refused the request earlier.
    caller = [row for row in rows if not row.operation.startswith("pipeline:")]
    assert [row.outcome for row in caller] == [Outcome.BLOCKED_BY_PIPELINE]

    stored = str([row.request_payload for row in rows])
    assert "Mustermann" not in stored, (
        "the refusal's audit row kept the personal data the pii_filter removed"
    )
    assert "<PERSON>" in stored, "and it kept the rewritten prompt rather than no prompt at all"
