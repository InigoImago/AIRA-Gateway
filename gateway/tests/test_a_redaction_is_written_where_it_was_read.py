"""A redaction is written where it was read: the same message, the same parts, in place (`FRD-309`).

A `pii_filter` that reads one text and writes another has two ways to go wrong, and both look like
success. Writing to a different message than it read records `redacted` — on the audit row, in the
caller's notice, in `X-AIRA-Pipeline` — while the model is sent the text as typed. Writing the
rewrite of several parts back as one moves it in front of everything else in the message: the model
reads *the question, then the image* where the caller sent *the image, then the question*, two
texts arrive glued without a space, and a Claude turn whose tool result came first now has it after
the text, which Anthropic refuses.

So the step reads one message — the last user message, or the last message where a request has
none — and asks about each of its text parts on its own, putting every rewrite back where its text
stood.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    TextPart,
    ToolResultPart,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

NAME = "Max Mustermann"
#: What the redactor below puts in place of the name.
PLACEHOLDER = "<PERSON>"


class _Redactor:
    """Replaces the name wherever it is in the text it was given, and keeps what it was given —
    a perfect redactor, so every difference below is the step's. Any other request is the caller's
    own, and its text parts are kept as what the model was sent."""

    is_test_double = True

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.sent: list[str] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("trusted", "trusted", ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        wrapped = request.messages[-1].text
        if not wrapped.startswith("<<<TEXT"):
            self.sent.extend(
                part.text
                for message in request.messages
                for part in message.parts
                if isinstance(part, TextPart)
            )
            return CanonicalResponse(
                model="trusted",
                text="ok",
                usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            )
        text = wrapped.split("\n", 1)[1].rsplit("\n", 1)[0]
        self.asked.append(text)
        return CanonicalResponse(
            model="trusted",
            text=text.replace(NAME, PLACEHOLDER),
            usage=CanonicalUsage(prompt_tokens=5, completion_tokens=5),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _redaction() -> Pipeline:
    return Pipeline(steps=(PipelineStep(StepType.PII_FILTER, {"model": "trusted"}),))


def _message(role: Role, *parts: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model="trusted", messages=[CanonicalMessage(role=role, parts=list(parts))]
    )


async def test_a_request_without_a_user_message_is_sent_as_it_is_recorded() -> None:
    """Its last message is the text the step reads — as the injection filter reads it — and so the
    text the step rewrites. The role a caller sends their text under is no way past the redactor."""
    redactor = _Redactor()
    request = _message(Role.SYSTEM, TextPart(text=f"Write to {NAME} about the invoice."))

    outcome = await PipelineEngine(ProviderRegistry([redactor])).run(_redaction(), request)

    assert outcome.decisions == [{"step": "pii_filter", "action": "redacted", "why": "trusted"}]
    assert [message.text for message in outcome.request.messages] == [
        f"Write to {PLACEHOLDER} about the invoice."
    ]


async def test_each_text_part_is_rewritten_where_it_stands() -> None:
    """Asked about part by part, so no rewrite has to be split back up — and nothing glues two
    texts together on the way."""
    redactor = _Redactor()
    image = DataPart(media_type="image/png", data=b"\x89PNG")
    request = _message(
        Role.USER,
        TextPart(text=f"Is this {NAME}'s ID card?"),
        image,
        TextPart(text=f"If it is, write to {NAME}."),
    )

    outcome = await PipelineEngine(ProviderRegistry([redactor])).run(_redaction(), request)

    parts = outcome.request.messages[-1].parts
    assert parts == [
        TextPart(text=f"Is this {PLACEHOLDER}'s ID card?"),
        image,
        TextPart(text=f"If it is, write to {PLACEHOLDER}."),
    ]
    assert redactor.asked == [f"Is this {NAME}'s ID card?", f"If it is, write to {NAME}."]
    assert outcome.rewrites == [
        (f"Is this {NAME}'s ID card?", f"Is this {PLACEHOLDER}'s ID card?"),
        (f"If it is, write to {NAME}.", f"If it is, write to {PLACEHOLDER}."),
    ]


async def test_a_tool_result_stays_in_front_of_the_text() -> None:
    """The order Anthropic requires of a turn that carries a tool result: the result first."""
    result = ToolResultPart(call_id="c1", name="lookup", content='{"ok": true}')
    request = _message(Role.USER, result, TextPart(text=f"Now write to {NAME}."))

    outcome = await PipelineEngine(ProviderRegistry([_Redactor()])).run(_redaction(), request)

    assert outcome.request.messages[-1].parts == [
        result,
        TextPart(text=f"Now write to {PLACEHOLDER}."),
    ]


async def test_only_the_last_user_message_is_read_where_there_is_one() -> None:
    """The fallback is for a request with no user message, and nothing wider: the use case's
    system instruction is not the redactor's to edit, and earlier turns stay the stated blind spot
    they were (`docs/REQUEST-LIFECYCLE.md`)."""
    redactor = _Redactor()
    request = CanonicalRequest(
        model="trusted",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text=f"You assist {NAME}'s team."),
            CanonicalMessage(role=Role.USER, text=f"Earlier: {NAME} called."),
            CanonicalMessage(role=Role.MODEL, text="Noted."),
            CanonicalMessage(role=Role.USER, text=f"Now write to {NAME}."),
        ],
    )

    outcome = await PipelineEngine(ProviderRegistry([redactor])).run(_redaction(), request)

    assert [message.text for message in outcome.request.messages] == [
        f"You assist {NAME}'s team.",
        f"Earlier: {NAME} called.",
        "Noted.",
        f"Now write to {PLACEHOLDER}.",
    ]
    assert redactor.asked == [f"Now write to {NAME}."]


async def test_a_request_with_no_message_has_nothing_to_redact_and_costs_nothing() -> None:
    """No message is no text: the step records that it changed nothing and asks no model."""
    redactor = _Redactor()

    outcome = await PipelineEngine(ProviderRegistry([redactor])).run(
        _redaction(), CanonicalRequest(model="trusted", messages=[])
    )

    assert outcome.decisions == [{"step": "pii_filter", "action": "unchanged", "why": "trusted"}]
    assert (redactor.asked, outcome.model_calls) == ([], [])


# == end to end: what the row says, what the model was sent, what is stored ======================


async def _served(contents: list[dict[str, Any]]) -> tuple[Any, _Redactor, list[RequestLog]]:
    redactor = _Redactor()

    class _Store:
        async def get(self, use_case: object) -> Pipeline:
            return _redaction()

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    registry = ProviderRegistry([redactor])
    app.state.providers = registry
    # The engine keeps its own registry reference from construction.
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="trusted",
                    capabilities=["generate", "attachments"],
                    attachments={"media_types": {"image/png": None}},
                )
            )
            await session.commit()
        response = client.post(
            "/v1beta/models/trusted:generateContent", json={"contents": contents}
        )
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())
    callers = [row for row in rows if not row.operation.startswith("pipeline:")]
    return response, redactor, callers


async def test_the_header_the_row_and_the_wire_agree_without_a_user_message() -> None:
    """Through the Gemini surface, `contents` holding only a `system` turn — which it accepts. The
    caller is told `redacted`, the row says it, and the model was sent what both of them claim."""
    response, redactor, callers = await _served(
        [{"role": "system", "parts": [{"text": f"Write to {NAME} today."}]}]
    )

    assert response.status_code == 200, response.text
    assert response.headers["x-aira-pipeline"] == "pii_filter=redacted"
    assert redactor.sent == [f"Write to {PLACEHOLDER} today."]
    assert [row.pipeline_decisions for row in callers] == [
        [{"step": "pii_filter", "action": "redacted", "why": "trusted"}]
    ]
    assert NAME not in str([row.request_payload for row in callers])


async def test_a_turn_of_several_parts_is_stored_as_it_was_sent() -> None:
    """Each part's rewrite replaces that part in the stored body (`FRD-309` FR-3), so the row keeps
    the request the model was sent: the parts in their order, each one redacted."""
    response, redactor, callers = await _served(
        [
            {
                "role": "user",
                "parts": [
                    {"text": f"Is this {NAME}'s ID card?"},
                    {"inlineData": {"mimeType": "image/png", "data": "iVBORw0KGgo="}},
                    {"text": f"If it is, write to {NAME}."},
                ],
            }
        ]
    )

    assert response.status_code == 200, response.text
    assert redactor.sent == [
        f"Is this {PLACEHOLDER}'s ID card?",
        f"If it is, write to {PLACEHOLDER}.",
    ]
    stored = callers[0].request_payload
    assert stored is not None
    assert [part.get("text") for part in stored["contents"][0]["parts"]] == [
        f"Is this {PLACEHOLDER}'s ID card?",
        None,
        f"If it is, write to {PLACEHOLDER}.",
    ]
