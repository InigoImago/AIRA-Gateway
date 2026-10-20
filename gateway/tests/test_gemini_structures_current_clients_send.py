"""What current Gemini clients send, served rather than refused (`FRD-145`).

Each case was a `400` from this surface for a body Google's own API accepts, found by reading what
OpenCode 1 and 2 and the AI SDK put on the wire:

- `includeThoughts` on a stream — OpenCode sends it on every stream to a thinking model;
- media in a function's result (`functionResponse.parts`) — OpenCode 2, when a tool reads an image;
- a parameter declared as a union without a `type`, or with `type: null` — every JSON Schema
  generator writes an optional argument so;
- `functionCallingConfig` other than `AUTO` — OpenCode 2 sends `NONE` on the last step of a
  bounded run, the AI SDK `VALIDATED` for strict tools.

And on the way to Google: a call and its result are paired by id where both carry one, calls
without an id stay distinct across a stream's chunks, and Model Armor's stop is a refusal.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    chunk_to_gemini,
    gemini_to_canonical,
)
from aira_gateway.app import create_app
from aira_gateway.attachments import AttachmentRejected
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    FinishReason,
    Role,
    ToolCallPart,
    ToolChoice,
    ToolDeclaration,
    ToolMode,
    ToolResultPart,
)
from aira_gateway.core.schema import SchemaRejected, parse, to_json_schema
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    GeminiStreamAssembler,
    canonical_to_gemini_request,
    gemini_response_to_canonical,
)
from aira_gateway.upstreams.openai.mapping import canonical_to_openai
from aira_gateway.upstreams.openai.mapping.response import StreamAssembler as OpenAIStreamAssembler

#: The PNG signature and a header: what the attachment checks read.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
_PNG_B64 = base64.b64encode(_PNG).decode("ascii")


def _parsed(**body: Any) -> CanonicalRequest:
    return gemini_to_canonical("gemini-x", schemas.GenerateContentRequest.model_validate(body))


_USER = {"role": "user", "parts": [{"text": "hi"}]}
_LOOK = {"functionDeclarations": [{"name": "look"}, {"name": "write"}]}


# == the surface ==================================================================================


@pytest.mark.parametrize("mode", ["AUTO", "ANY", "NONE", "VALIDATED", "any", "MODE_UNSPECIFIED"])
def test_every_mode_google_has_is_accepted(mode: str) -> None:
    request = _parsed(
        contents=[_USER], tools=[_LOOK], toolConfig={"functionCallingConfig": {"mode": mode}}
    )
    expected = (
        None
        if mode.upper() in {"AUTO", "MODE_UNSPECIFIED"}
        else ToolChoice(mode=ToolMode(mode.lower()))
    )

    assert request.tool_choice == expected


def test_allowed_names_travel_with_the_mode() -> None:
    request = _parsed(
        contents=[_USER],
        tools=[_LOOK],
        toolConfig={"functionCallingConfig": {"mode": "ANY", "allowed_function_names": ["write"]}},
    )

    assert request.tool_choice == ToolChoice(mode=ToolMode.ANY, allowed=("write",))


@pytest.mark.parametrize(
    ("calling", "named"),
    [
        ({"mode": "SOMETIMES"}, "not a function-calling mode"),
        ({"mode": "NONE", "allowedFunctionNames": ["look"]}, "makes no call"),
        ({"mode": "ANY", "allowedFunctionNames": ["delete"]}, "does not declare"),
    ],
)
def test_a_mode_that_cannot_be_honoured_is_refused_by_name(
    calling: dict[str, Any], named: str
) -> None:
    with pytest.raises(ValidationError, match=named):
        schemas.GenerateContentRequest.model_validate(
            {
                "contents": [_USER],
                "tools": [_LOOK],
                "toolConfig": {"functionCallingConfig": calling},
            }
        )


def test_a_required_call_needs_a_function_to_call() -> None:
    with pytest.raises(ValidationError, match="no function is declared"):
        schemas.GenerateContentRequest.model_validate(
            {"contents": [_USER], "toolConfig": {"functionCallingConfig": {"mode": "ANY"}}}
        )


def _with_a_result(*media: dict[str, Any]) -> dict[str, Any]:
    return {
        "contents": [
            _USER,
            {"role": "model", "parts": [{"functionCall": {"name": "look", "args": {}}}]},
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "look",
                            "response": {"ok": True},
                            "parts": list(media),
                        }
                    }
                ],
            },
        ],
        "tools": [_LOOK],
    }


def test_media_in_a_functions_result_is_carried_and_checked_like_any_attachment() -> None:
    request = _parsed(**_with_a_result({"inlineData": {"mimeType": "image/png", "data": _PNG_B64}}))

    result = request.messages[2].tool_results[0]
    assert result.attachments == (DataPart(media_type="image/png", data=_PNG),)
    # Counted where the limits and the model's media types are checked.
    assert request.media_types == frozenset({"image/png"})
    with pytest.raises(AttachmentRejected):
        _parsed(
            **_with_a_result(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(b"GIF89a").decode(),
                    }
                }
            )
        )


def test_a_turn_that_was_only_thoughts_is_left_out() -> None:
    request = _parsed(
        contents=[
            _USER,
            {"role": "model", "parts": [{"text": "thinking…", "thought": True}]},
            {"role": "user", "parts": [{"text": "and?"}]},
        ]
    )

    assert [message.role for message in request.messages] == [Role.USER, Role.USER]


def test_a_signature_on_text_is_accepted() -> None:
    """Gemini 3 signs its first text part and google-genai replays it; Google needs none there."""
    request = _parsed(
        contents=[
            _USER,
            {"role": "model", "parts": [{"text": "hello", "thoughtSignature": "c2ln"}]},
            {"role": "user", "parts": [{"text": "again"}]},
        ]
    )

    assert request.messages[1].text == "hello"


# == schemas ======================================================================================


def test_a_union_without_a_type_and_a_null_type_are_parameters_google_accepts() -> None:
    parameters = parse(
        {
            "type": "object",
            "properties": {"path": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
    )

    assert to_json_schema(parameters)["properties"]["path"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }
    assert parameters.to_wire()["properties"]["path"] == {
        "anyOf": [{"type": "STRING"}, {"type": "NULL"}]
    }


def test_only_a_union_may_leave_the_type_out() -> None:
    with pytest.raises(SchemaRejected, match="anyOf"):
        parse({"type": "object", "properties": {"path": {"description": "no type"}}})


# == to Google ====================================================================================


def _call_and_result(call_id: str, result_id: str) -> CanonicalRequest:
    return CanonicalRequest(
        model="gemini-x",
        messages=[
            CanonicalMessage(role=Role.USER, text="hi"),
            CanonicalMessage(role=Role.MODEL, parts=[ToolCallPart(id=call_id, name="look")]),
            CanonicalMessage(
                role=Role.USER,
                parts=[ToolResultPart(call_id=result_id, name="look", content="{}")],
            ),
        ],
    )


def test_a_call_and_its_result_carry_the_id_that_pairs_them() -> None:
    """Measured on Vertex: a pair sent with the id both halves carry is answered."""
    body = canonical_to_gemini_request(_call_and_result("look-1", "look"))

    assert body["contents"][1]["parts"][0]["functionCall"]["id"] == "look-1"
    assert body["contents"][2]["parts"][0]["functionResponse"]["id"] == "look-1"


def test_an_id_on_one_half_only_is_not_sent() -> None:
    """A result answering another conversation's call cannot be paired by id; by name it can."""
    request = _call_and_result("look-1", "elsewhere").model_copy(
        update={
            "messages": [
                *_call_and_result("look-1", "elsewhere").messages[:2],
                CanonicalMessage(
                    role=Role.USER,
                    parts=[ToolResultPart(call_id="elsewhere", name="other", content="{}")],
                ),
            ]
        }
    )
    body = canonical_to_gemini_request(request)

    assert "id" not in body["contents"][1]["parts"][0]["functionCall"]
    assert "id" not in body["contents"][2]["parts"][0]["functionResponse"]


def test_media_in_a_result_goes_to_google_in_the_results_parts() -> None:
    request = _call_and_result("look-1", "look-1")
    request.messages[2].parts[0] = ToolResultPart(
        call_id="look-1",
        name="look",
        content="{}",
        attachments=(DataPart(media_type="image/png", data=_PNG),),
    )

    response = canonical_to_gemini_request(request)["contents"][2]["parts"][0]["functionResponse"]

    assert response["parts"] == [{"inlineData": {"mimeType": "image/png", "data": _PNG_B64}}]


def test_a_dialect_whose_tool_messages_are_text_refuses_media_in_one() -> None:
    request = _call_and_result("look-1", "look-1")
    request.messages[2].parts[0] = ToolResultPart(
        call_id="look-1",
        name="look",
        content="{}",
        attachments=(DataPart(media_type="image/png", data=_PNG),),
    )

    with pytest.raises(DialectUnsupported, match="image/png"):
        canonical_to_openai(request)


def test_the_tool_mode_goes_to_google_as_google_spells_it() -> None:
    request = CanonicalRequest(
        model="gemini-x",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        tools=(ToolDeclaration(name="look"),),
        tool_choice=ToolChoice(mode=ToolMode.ANY, allowed=("look",)),
    )

    assert canonical_to_gemini_request(request)["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["look"]}
    }
    assert "toolConfig" not in canonical_to_gemini_request(
        request.model_copy(update={"tool_choice": None})
    )


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (ToolChoice(mode=ToolMode.NONE), "none"),
        (ToolChoice(mode=ToolMode.ANY), "required"),
        (
            ToolChoice(mode=ToolMode.ANY, allowed=("look",)),
            {"type": "function", "function": {"name": "look"}},
        ),
    ],
)
def test_the_tool_mode_on_the_openai_dialect(choice: ToolChoice, expected: Any) -> None:
    request = CanonicalRequest(
        model="m",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        tools=(ToolDeclaration(name="look"), ToolDeclaration(name="write")),
        tool_choice=choice,
    )

    assert canonical_to_openai(request)["tool_choice"] == expected
    with pytest.raises(DialectUnsupported, match="VALIDATED"):
        canonical_to_openai(
            request.model_copy(update={"tool_choice": ToolChoice(mode=ToolMode.VALIDATED)})
        )


def test_calls_without_an_id_stay_distinct_across_a_streams_chunks() -> None:
    """Two calls of one function in two chunks were both `look-0`, and a result answered both."""
    assembler = GeminiStreamAssembler()

    def chunk() -> dict[str, Any]:
        part = {"functionCall": {"name": "look", "args": {}}}
        return {"candidates": [{"content": {"role": "model", "parts": [part]}}]}

    first = assembler.feed(chunk()).tool_calls[0].id
    second = assembler.feed(chunk()).tool_calls[0].id

    assert first != second


def test_a_streamed_thought_from_google_is_kept_apart_from_the_answer() -> None:
    parts = [{"text": "hm", "thought": True}, {"text": "yes"}]
    chunk = GeminiStreamAssembler().feed({"candidates": [{"content": {"parts": parts}}]})

    assert (chunk.reasoning_delta, chunk.text_delta) == ("hm", "yes")


def test_model_armor_stopping_an_answer_is_a_refusal() -> None:
    data = {"candidates": [{"content": {"parts": []}, "finishReason": "MODEL_ARMOR"}]}

    assert gemini_response_to_canonical(data, "gemini-x").finish_reason is FinishReason.REFUSAL


# == back to the client ===========================================================================


def test_the_cached_share_of_the_prompt_is_reported() -> None:
    usage = CanonicalUsage(prompt_tokens=100, completion_tokens=5, cached_input_tokens=80)
    answer = canonical_to_gemini(CanonicalResponse(model="m", text="ok", usage=usage))
    chunk = chunk_to_gemini(CanonicalChunk(text_delta="ok", usage=usage), "m")

    assert answer.usageMetadata is not None and chunk.usageMetadata is not None
    assert answer.usageMetadata.cachedContentTokenCount == 80
    assert chunk.usageMetadata.cachedContentTokenCount == 80
    assert (
        "cachedContentTokenCount"
        not in canonical_to_gemini(
            CanonicalResponse(
                model="m", text="ok", usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1)
            )
        ).model_dump(exclude_none=True)["usageMetadata"]
    )


def test_a_streamed_thought_is_a_thought_part_before_the_text() -> None:
    parts = (
        chunk_to_gemini(CanonicalChunk(text_delta="yes", reasoning_delta="hm"), "m")
        .candidates[0]
        .content.parts
    )

    assert [(part.text, part.thought) for part in parts] == [("hm", True), ("yes", None)]
    only = chunk_to_gemini(CanonicalChunk(text_delta="", reasoning_delta="hm"), "m")
    assert [part.text for part in only.candidates[0].content.parts] == ["hm"]


def test_the_openai_dialect_streams_reasoning_only_where_it_was_asked_for() -> None:
    payload = {"choices": [{"delta": {"reasoning_content": "hm", "content": ""}}]}

    shown = OpenAIStreamAssembler(include_reasoning=True).feed(payload)
    withheld = OpenAIStreamAssembler().feed(payload)

    assert shown is not None and shown.reasoning_delta == "hm"
    assert withheld is not None and withheld.reasoning_delta == ""


class _ThinksAloud:
    """An upstream that streams a thought and an answer, as Gemini does with `includeThoughts`."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent", "streamGenerateContent"))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:  # pragma: no cover
        raise NotImplementedError

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        yield CanonicalChunk(text_delta="", reasoning_delta="91 is 7 times 13")
        yield CanonicalChunk(
            text_delta="No.",
            finish_reason=FinishReason.STOP,
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=9, reasoning_tokens=6),
        )

    async def embed(self, request: object) -> list[list[float]]:  # pragma: no cover
        return [[0.0]]


async def test_a_stream_hands_the_thoughts_to_the_caller() -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([_ThinksAloud()])
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", capabilities=["generate"], publisher="google"))
            await session.commit()
        response = client.post(
            "/v1beta/models/mock-1:streamGenerateContent?alt=sse",
            json={"contents": [_USER]},
        )

    assert response.status_code == 200, response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    parts = [part for event in events for part in event["candidates"][0]["content"]["parts"]]
    assert parts[0] == {"text": "91 is 7 times 13", "thought": True}
    assert parts[-1] == {"text": "No."}
