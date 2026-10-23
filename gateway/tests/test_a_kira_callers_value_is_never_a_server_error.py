"""On the KIRA surface, a caller's value is never a server error — and never a missing record.

Two values a client can send turned a request into a `500` with no audit row, both inside the
function-calling fields: a part whose `type` is a list, and a parameter whose `type` is
`["string", "null"]` — ordinary JSON Schema, what generators write for an optional argument. Both
were looked up in a set, which cannot hold a list. The second is now served; the first is the
contract's `422`.

The rest of the file is the sweep `LESSONS.md` §1 asks for, over the fields these two came from:
every field of a part, a function and a parameter schema, with every kind of JSON value. It asserts
only the floor — no `500`, and a row — because what each field *should* answer is pinned where the
field is tested.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.mapping import to_canonical
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead

BASE = "/kira/api/external"
UC = {"x-aira-use-case": "uc"}

#: One of each kind a JSON value can be that a field might not expect.
KINDS: list[Any] = [[], ["x"], {}, {"a": 1}, 0, -1, 1.5, True, None, "", "x" * 300]


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _ready(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
        session.add(ModelRead(model="mock-1", numeric_id=1004, capabilities=["generate", "tools"]))
        await session.commit()


async def _rows(app: Any) -> int:
    async with app.state.db_sessionmaker() as session:
        return int((await session.execute(select(func.count(RequestLog.id)))).scalar_one())


def _client(app: Any) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


TOOL: dict[str, Any] = {
    "name": "lookup",
    "description": "Look a customer up.",
    "parameters": {"type": "object", "properties": {"email": {"type": "string"}}},
}


def _chat(**over: Any) -> dict[str, Any]:
    return {"request": {"parts": [{"text": "Wer ist das?"}]}, "model_id": 1004, **over}


def _with_parameter(schema: Any) -> dict[str, Any]:
    return _chat(
        tools=[{**TOOL, "parameters": {"type": "object", "properties": {"email": schema}}}]
    )


# == the two values the audit found ===============================================================


@pytest.mark.parametrize("kind", [[], {}, ["function_call"], 1, True])
async def test_a_part_type_that_is_not_a_word_is_a_validation_error_on_the_record(
    kind: Any,
) -> None:
    app = _app()
    with _client(app) as client:
        await _ready(app)
        body = _chat(request={"parts": [{"type": kind, "text": "hi"}]})
        response = client.post(f"{BASE}/chat", json=body, headers=UC)
        rows = await _rows(app)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "'type' must be a string" in response.text
    assert rows == 1


@pytest.mark.parametrize("nullable", [["string", "null"], ["null", "integer"], ["array", "null"]])
async def test_a_nullable_parameter_is_served_and_carried_as_written(nullable: list[str]) -> None:
    """JSON Schema's own way to say *this, or null*, and Google's documented one: every dialect
    this gateway sends parameters to reads it, so it is carried as written rather than translated
    into a keyword one of them would ignore."""
    schema: dict[str, Any] = {"type": nullable}
    if "array" in nullable:
        schema["items"] = {"type": "string"}
    body = _with_parameter(schema)
    app = _app()
    with _client(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 200, response.text
    (declaration,) = to_canonical(schemas.ChatRequest.model_validate(body), "mock-1").tools
    assert declaration.parameters_json_schema is not None
    assert declaration.parameters_json_schema.to_wire() == body["tools"][0]["parameters"]


@pytest.mark.parametrize(
    "kind",
    [
        ["string", "integer"],
        ["null"],
        ["null", "null"],
        ["string"],
        [["string"], "null"],
        [{"type": "string"}, "null"],
        ["string", "null", "integer"],
        [],
        {},
        {"string": "null"},
        "text",
    ],
)
async def test_any_other_type_is_the_contract_s_invalid_tool_configuration(kind: Any) -> None:
    app = _app()
    with _client(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_with_parameter({"type": kind}), headers=UC)
        rows = await _rows(app)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_TOOL_CONFIGURATION"
    assert "parameters.properties.email.type" in response.text
    assert rows == 1


# == the sweep ====================================================================================


def _call(**over: Any) -> dict[str, Any]:
    return {"type": "function_call", "id": "c1", "name": "lookup", "arguments": {}, **over}


def _result(**over: Any) -> dict[str, Any]:
    return {"type": "function_result", "call_id": "c1", "name": "lookup", "result": {}, **over}


def _turns(call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return _chat(
        tools=[TOOL],
        conversation_history=[
            {"role": "user", "content": {"parts": [{"text": "Wer?"}]}},
            {"role": "model", "content": {"parts": [call]}},
        ],
        request={"parts": [result]},
    )


def _bodies() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []
    for value in KINDS:
        shown = f"{value!r:.20}"
        for field in ("type", "id", "name", "arguments", "opaque_metadata"):
            body = _turns(_call(**{field: value}), _result())
            cases.append((f"call.{field}={shown}", body))
        for field in ("type", "call_id", "name", "result", "error"):
            body = _turns(_call(), _result(**{field: value}))
            cases.append((f"result.{field}={shown}", body))
        for field in ("type", "text", "mime_type", "data"):
            cases.append((f"part.{field}={shown}", _chat(request={"parts": [{field: value}]})))
        for field in ("type", "name", "description", "parameters"):
            cases.append((f"tool.{field}={shown}", _chat(tools=[{**TOOL, field: value}])))
        for keyword in ("type", "properties", "required", "items", "anyOf", "enum", "nullable"):
            parameter = {"type": "string", keyword: value}
            cases.append((f"schema.{keyword}={shown}", _with_parameter(parameter)))
    return cases


BODIES = _bodies()


async def test_no_value_in_a_function_field_is_a_server_error_or_an_unrecorded_refusal() -> None:
    """One app for the whole sweep: a row per request, counted as it goes, so a refusal that left
    none is named by the case that produced it."""
    app = _app()
    failures: list[str] = []
    with _client(app) as client:
        await _ready(app)
        for label, body in BODIES:
            before = await _rows(app)
            response = client.post(f"{BASE}/chat", json=body, headers=UC)
            after = await _rows(app)
            if response.status_code >= 500:
                failures.append(f"{label}: {response.status_code}")
            elif after != before + 1:
                failures.append(f"{label}: {response.status_code} and no row")

    assert len(BODIES) == 25 * len(KINDS), "the sweep has the cases it is named for"
    assert not failures, failures
