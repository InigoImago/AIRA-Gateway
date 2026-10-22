"""Function calling on the KIRA surface (`FRD-146` FR-1 to FR-6).

A migrating agent declares its functions, receives a call, runs it and sends the result back — and
must not change a line to do it here. Each rule of §5.1 has a test, and the round trip runs through
the mock, buffered and streamed, because the mock calls the first function it is offered.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.mapping import to_canonical, to_chat_response
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalResponse,
    CanonicalUsage,
    Role,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead

BASE = "/kira/api/external"
UC = {"x-aira-use-case": "agent-uc"}

WEATHER = {
    "name": "get_weather",
    "description": "The weather in a city",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


def _app():  # noqa: ANN202
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _ready(app, *, tools_enabled: bool = True) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="agent-uc", name="agent-uc", tools_enabled=tools_enabled))
        session.add(
            ModelRead(
                model="mock-1",
                numeric_id=1004,
                capabilities=["generate", "tools"],
                publisher="google",
            )
        )
        await session.commit()


def _chat(**over: Any) -> dict[str, Any]:
    return {
        "request": {"parts": [{"text": "Wie ist das Wetter in Bonn?"}]},
        "model_id": 1004,
        "tools": [WEATHER],
        **over,
    }


def _call(**over: Any) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": "call-1",
        "name": "get_weather",
        "arguments": {"city": "Bonn"},
        **over,
    }


def _result(**over: Any) -> dict[str, Any]:
    return {
        "type": "function_result",
        "id": "call-1",
        "name": "get_weather",
        "result": {"temperature": 14},
        **over,
    }


def _continued(call: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> dict:
    """The second request of the loop: the model's call in the history, the result as the turn."""
    return _chat(
        conversation_history=[
            {"role": "user", "content": {"parts": [{"text": "Wie ist das Wetter in Bonn?"}]}},
            {"role": "model", "content": {"parts": [call or _call()]}},
        ],
        request={"parts": [result or _result()]},
    )


# == the round trip ===============================================================================


async def test_a_declared_function_comes_back_as_a_call() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_chat(), headers=UC)
        async with app.state.db_sessionmaker() as session:
            row = (await session.execute(select(RequestLog))).scalars().one()

    assert response.status_code == 200, response.text
    (part,) = [p for p in response.json()["parts"] if p.get("type") == "function_call"]
    assert set(part) == {"type", "id", "name", "arguments"}
    assert part["name"] == "get_weather"
    assert isinstance(part["arguments"], dict)
    # On the audit row like a call on the Gemini surface: names, never arguments.
    assert row.tool_calls == {"declared": 1, "called": ["get_weather"]}


async def test_the_result_goes_back_and_the_model_answers() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_continued(), headers=UC)
        async with app.state.db_sessionmaker() as session:
            row = (await session.execute(select(RequestLog))).scalars().one()

    assert response.status_code == 200, response.text
    assert response.json()["parts"][0]["text"].endswith("acted on the tool result")
    assert row.tool_calls == {"declared": 1, "called": []}


async def test_a_stream_sends_each_call_before_completed_and_completed_carries_it() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        with client.stream("POST", f"{BASE}/streaming-chat", json=_chat(), headers=UC) as stream:
            assert stream.status_code == 200
            body = "".join(stream.iter_text())

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.split("\n\n")
        if line.startswith("data: ")
    ]
    statuses = [event["status"] for event in events]
    assert statuses[-2:] == ["function_call", "completed"]
    assert events[-2]["data"]["name"] == "get_weather"
    calls = [p for p in events[-1]["data"]["parts"] if p.get("type") == "function_call"]
    assert [call["id"] for call in calls] == [events[-2]["data"]["id"]]


# == the signature travels there and back (FR-4) ==================================================


def _answer_with_a_signature() -> CanonicalResponse:
    return CanonicalResponse(
        model="m",
        text="",
        usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        tool_calls=(
            ToolCallPart(id="c1", name="get_weather", arguments={"city": "Bonn"}, signature="SIG"),
        ),
    )


def test_a_calls_signature_rides_in_opaque_metadata() -> None:
    (part,) = to_chat_response(_answer_with_a_signature()).parts

    assert isinstance(part, schemas.FunctionCallPart)
    assert part.opaque_metadata == {"thought_signature": "SIG"}


def test_a_replayed_call_carries_its_signature_back_to_the_model() -> None:
    """A thinking model refuses its own call replayed without the signature it issued."""
    request = schemas.ChatRequest.model_validate(
        _continued(call=_call(opaque_metadata={"thought_signature": "SIG"}))
    )

    model_turn = to_canonical(request, "mock-1").messages[1]

    assert model_turn.role is Role.MODEL
    assert model_turn.tool_calls[0].signature == "SIG"


async def test_the_signature_is_not_stored_with_the_answer() -> None:
    from aira_gateway.api.kira.mapping import stored

    payload = to_chat_response(_answer_with_a_signature()).model_dump(exclude_none=True)

    assert payload["parts"][0]["opaque_metadata"] == {"thought_signature": "SIG"}
    assert "opaque_metadata" not in stored(payload)["parts"][0]


def test_a_result_is_mapped_with_its_call_and_a_structured_result_as_json() -> None:
    request = schemas.ChatRequest.model_validate(_continued())

    (result,) = to_canonical(request, "mock-1").messages[-1].parts

    assert isinstance(result, ToolResultPart)
    assert (result.call_id, result.name) == ("call-1", "get_weather")
    assert json.loads(result.content) == {"temperature": 14}


def test_a_text_result_is_sent_as_the_text_it_is() -> None:
    request = schemas.ChatRequest.model_validate(_continued(result=_result(result="14 Grad")))

    (result,) = to_canonical(request, "mock-1").messages[-1].parts

    assert isinstance(result, ToolResultPart) and result.content == "14 Grad"


def test_a_turn_with_function_parts_keeps_its_order() -> None:
    """Documents go first in an ordinary turn (FR-18). A function turn is the conversation, and
    its text stays where it was written."""
    request = schemas.ChatRequest.model_validate(
        _continued(result=_result())
        | {"request": {"parts": [{"text": "vorher"}, _result(), {"text": "nachher"}]}}
    )

    parts = to_canonical(request, "mock-1").messages[-1].parts

    assert [type(part).__name__ for part in parts] == ["TextPart", "ToolResultPart", "TextPart"]


# == which part may stand where (FR-2) ============================================================


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            _chat(request={"parts": [_call()]}),
            "a call in the caller's own turn",
        ),
        (
            _chat(
                conversation_history=[{"role": "user", "content": {"parts": [_call()]}}],
            ),
            "a call in a user turn",
        ),
        (
            _chat(
                conversation_history=[{"role": "model", "content": {"parts": [_result()]}}],
            ),
            "a result in a model turn",
        ),
        (
            _chat(system_instruction={"parts": [_result()]}),
            "a function part in the system instruction",
        ),
        (_chat(request={"parts": [_call(type="function_thing")]}), "an unknown part type"),
        (_chat(request={"parts": [_result(id="")]}), "a result without an id"),
        (_chat(request={"parts": [_result(name=7)]}), "a name that is not a string"),
        (
            _chat(request={"parts": [{k: v for k, v in _result().items() if k != "result"}]}),
            "a result without its result",
        ),
        (
            _continued(call=_call(arguments="city=Bonn")),
            "arguments that are not an object",
        ),
        (
            _continued(call=_call(opaque_metadata="SIG")),
            "metadata that is not an object",
        ),
    ],
)
async def test_a_part_in_the_wrong_place_is_a_validation_error(
    body: dict[str, Any], why: str
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 422, why
    assert response.json()["code"] == "VALIDATION_ERROR", why


# == the declaration (FR-1, FR-6) =================================================================


@pytest.mark.parametrize(
    ("tools", "why"),
    [
        ([{**WEATHER, "name": "1weather"}], "a name starting with a digit"),
        ([{**WEATHER, "name": "get weather"}], "a name with a space"),
        ([{**WEATHER, "name": "x" * 65}], "a name of 65 characters"),
        ([WEATHER, WEATHER], "the same name twice"),
        ([{**WEATHER, "parameters": {"type": "string"}}], "parameters that are not an object"),
        ([{**WEATHER, "parameters": {"type": "object", "properties": "x" * 300_000}}], "too big"),
        ([{**WEATHER, "name": f"f{index}"} for index in range(129)], "129 functions"),
    ],
)
async def test_a_declaration_it_cannot_take_is_invalid_tool_configuration(
    tools: list[dict[str, Any]], why: str
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_chat(tools=tools), headers=UC)

    assert response.status_code == 422, f"{why}: {response.text[:200]}"
    assert response.json()["code"] == "INVALID_TOOL_CONFIGURATION", why


async def test_function_parts_without_tools_are_invalid_tool_configuration() -> None:
    app = _app()
    body = _continued()
    del body["tools"]
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOOL_CONFIGURATION"


async def test_a_parameterless_function_is_a_declaration_too() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(
            f"{BASE}/chat",
            json=_chat(tools=[{"name": "now", "description": "the time"}]),
            headers=UC,
        )

    assert response.status_code == 200, response.text


async def test_a_use_case_without_tool_calling_refuses_in_the_contracts_code() -> None:
    """Functions stay behind the use case's switch, as on the Gemini surface (`FRD-131` FR-3)."""
    app = _app()
    with TestClient(app) as client:
        await _ready(app, tools_enabled=False)
        response = client.post(f"{BASE}/chat", json=_chat(), headers=UC)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOOL_CONFIGURATION"
    assert "agent-uc" in response.json()["message"]


async def test_without_a_use_case_functions_are_refused_too() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOOL_CONFIGURATION"


# == a result answers a call (FR-3) ===============================================================


@pytest.mark.parametrize(
    ("result", "why", "said"),
    [
        (_result(id="call-2"), "an id no call has", "answers no function call"),
        (
            _result(name="get_time"),
            "the right id under another function's name",
            "but that call was to",
        ),
    ],
)
async def test_a_result_that_answers_no_call_is_invalid_function_response(
    result: dict[str, Any], why: str, said: str
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(
            f"{BASE}/chat",
            json=_continued(result=result) | {"tools": [WEATHER, {"name": "get_time"}]},
            headers=UC,
        )

    assert response.status_code == 422, why
    assert response.json()["code"] == "INVALID_FUNCTION_RESPONSE", why
    # Named for what is wrong with it: an orphan result is not a call to another function.
    assert said in response.json()["message"], why


async def test_a_call_answered_twice_is_invalid_function_response() -> None:
    app = _app()
    with TestClient(app) as client:
        await _ready(app)
        body = _continued()
        body["request"]["parts"].append(_result())
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_FUNCTION_RESPONSE"


async def test_a_result_to_an_earlier_turns_call_is_invalid_function_response() -> None:
    """A result answers the model turn **before** it. A later model turn closes the calls of the
    one before, whoever answers them now is answering a question nobody is asking any more."""
    app = _app()
    body = _chat(
        conversation_history=[
            {"role": "user", "content": {"parts": [{"text": "Wetter?"}]}},
            {"role": "model", "content": {"parts": [_call()]}},
            {"role": "user", "content": {"parts": [{"text": "egal"}]}},
            {"role": "model", "content": {"parts": [{"text": "gut"}]}},
        ],
        request={"parts": [_result()]},
    )
    with TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_FUNCTION_RESPONSE"
