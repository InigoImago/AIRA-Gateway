"""What current Gemini clients send, carried without breaking what older ones send (`FRD-143`).

The AI SDK's Google provider declares every tool in JSON Schema (`parametersJsonSchema`) since its
fourth major version, asks for structured output the same way (`responseJsonSchema`) and sets
Gemini 3's `thinkingLevel`; its earlier majors, and the `google-genai` SDK by default, send the
OpenAPI subset (`parameters`, `responseSchema`). Both are Google's own API, and both must work.

JSON Schema is carried **as written** to every dialect, because each takes it natively; what this
gateway adds is the bound, and the refusal of a declaration made twice.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import gemini_to_canonical
from aira_gateway.api.serving.controls import prompt_texts
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, ToolDeclaration
from aira_gateway.core.json_schema import parse_json_schema
from aira_gateway.core.schema import SchemaBounds, SchemaRejected
from aira_gateway.db.models import ModelRead, UseCaseRead
from aira_gateway.upstreams.diagnosis import from_body
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request
from aira_gateway.upstreams.openai.mapping import canonical_to_openai
from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

#: What a zod schema becomes in the AI SDK: draft-07 keywords Google's OpenAPI subset does not
#: have (`$schema`, `exclusiveMinimum`, `additionalProperties`) and that every dialect takes.
JSON_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "the file to read"},
        "limit": {"type": "integer", "exclusiveMinimum": 0},
    },
    "required": ["path"],
    "additionalProperties": False,
}

OPENAPI = {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}}

USER = [{"role": "user", "parts": [{"text": "read hello.py"}]}]


def _request(**extra: Any) -> schemas.GenerateContentRequest:
    return schemas.GenerateContentRequest.model_validate({"contents": USER, **extra})


def _tools(*declarations: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"functionDeclarations": list(declarations)}]


def _canonical(**extra: Any) -> CanonicalRequest:
    return gemini_to_canonical("m", _request(**extra))


# ---- FR-1: parametersJsonSchema ------------------------------------------------------------------


@pytest.mark.parametrize("field", ["parametersJsonSchema", "parameters_json_schema"])
def test_a_json_schema_declaration_is_carried_as_written(field: str) -> None:
    canonical = _canonical(tools=_tools({"name": "read_file", field: JSON_SCHEMA}))

    (tool,) = canonical.tools
    assert tool.parameters is None
    assert tool.parameters_json_schema is not None
    assert tool.parameters_json_schema.document == JSON_SCHEMA


def test_the_openapi_declaration_older_clients_send_still_parses() -> None:
    (tool,) = _canonical(tools=_tools({"name": "read_file", "parameters": OPENAPI})).tools

    assert tool.parameters is not None and tool.parameters_json_schema is None


def test_parameters_declared_twice_are_refused_by_name() -> None:
    with pytest.raises(ValidationError, match="declares its parameters twice"):
        _request(
            tools=_tools(
                {"name": "read_file", "parameters": OPENAPI, "parametersJsonSchema": JSON_SCHEMA}
            )
        )


@pytest.mark.parametrize(
    ("document", "said"),
    [
        (["not", "an", "object"], "must be an object"),
        ({"type": "object", "description": "x" * 40_000}, "bytes, above"),
        ({"type": "object", "properties": {f"p{i}": {} for i in range(300)}}, "properties, above"),
    ],
)
def test_a_json_schema_is_bounded_like_any_other_schema(document: Any, said: str) -> None:
    with pytest.raises(SchemaRejected, match=said):
        parse_json_schema(document, SchemaBounds(), what="The JSON schema of 'read_file'")


def test_a_json_schema_nested_too_deep_is_refused() -> None:
    node: dict[str, Any] = {"type": "string"}
    for _ in range(10):
        node = {"type": "object", "properties": {"a": node}}

    with pytest.raises(SchemaRejected, match="nests deeper than 8 levels"):
        parse_json_schema(node, SchemaBounds())
    assert parse_json_schema(JSON_SCHEMA, SchemaBounds()).document == JSON_SCHEMA


def test_a_dialect_cannot_change_the_request_it_was_given() -> None:
    document = parse_json_schema(JSON_SCHEMA)

    document.to_wire()["properties"]["path"]["type"] = "number"

    assert document.document["properties"]["path"]["type"] == "string"


# ---- FR-2: responseJsonSchema --------------------------------------------------------------------


def test_a_json_schema_for_the_answer_is_carried_as_written() -> None:
    canonical = _canonical(
        generationConfig={"responseMimeType": "application/json", "responseJsonSchema": JSON_SCHEMA}
    )

    assert canonical.response_schema is None
    assert canonical.response_json_schema is not None
    assert canonical.response_json_schema.document == JSON_SCHEMA
    assert canonical.structured


def test_a_json_schema_for_the_answer_needs_the_json_media_type() -> None:
    with pytest.raises(ValidationError, match="needs 'responseMimeType' application/json"):
        _request(generationConfig={"responseJsonSchema": JSON_SCHEMA})


def test_both_answer_schemas_are_refused() -> None:
    with pytest.raises(ValidationError, match="are both set"):
        _request(
            generationConfig={
                "responseMimeType": "application/json",
                "responseSchema": OPENAPI,
                "responseJsonSchema": JSON_SCHEMA,
            }
        )


# ---- FR-3: thinkingLevel -------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["thinkingLevel", "thinking_level"])
def test_gemini_3s_level_word_is_the_canonical_mode(field: str) -> None:
    canonical = _canonical(generationConfig={"thinkingConfig": {field: "HIGH"}})

    assert canonical.thinking is not None
    assert (canonical.thinking.mode, canonical.thinking.tokens) == ("high", None)


def test_a_level_beside_a_budget_is_refused() -> None:
    with pytest.raises(ValidationError, match="not both"):
        _request(generationConfig={"thinkingConfig": {"thinkingLevel": "low", "thinkingBudget": 9}})


# ---- FR-4: every dialect takes JSON Schema as written --------------------------------------------


def _with_json_schemas() -> CanonicalRequest:
    return _canonical(
        tools=_tools({"name": "read_file", "parametersJsonSchema": JSON_SCHEMA}),
        generationConfig={
            "responseMimeType": "application/json",
            "responseJsonSchema": JSON_SCHEMA,
        },
    )


def test_gemini_gets_the_json_schema_fields() -> None:
    body = canonical_to_gemini_request(_with_json_schemas())

    (declaration,) = body["tools"][0]["functionDeclarations"]
    assert declaration["parametersJsonSchema"] == JSON_SCHEMA
    assert "parameters" not in declaration
    assert body["generationConfig"]["responseJsonSchema"] == JSON_SCHEMA
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" not in body["generationConfig"]


def test_gemini_still_gets_the_openapi_fields_an_older_client_sent() -> None:
    body = canonical_to_gemini_request(
        _canonical(tools=_tools({"name": "read_file", "parameters": OPENAPI}))
    )

    (declaration,) = body["tools"][0]["functionDeclarations"]
    assert "parameters" in declaration and "parametersJsonSchema" not in declaration


def test_the_openai_dialect_gets_the_json_schema_and_no_strict() -> None:
    body = canonical_to_openai(_with_json_schemas())

    assert body["tools"][0]["function"]["parameters"] == JSON_SCHEMA
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": body["response_format"]["json_schema"]["name"],
            "strict": False,
            "schema": JSON_SCHEMA,
        },
    }


def test_the_anthropic_dialect_gets_the_json_schema_and_never_strict() -> None:
    body = canonical_to_anthropic(_with_json_schemas(), max_tokens=1024, strict_tools=True)

    assert body["tools"][0]["input_schema"] == JSON_SCHEMA
    assert "strict" not in body["tools"][0], "a schema written by hand is not rewritten for strict"
    assert body["output_config"]["format"]["schema"] == JSON_SCHEMA


def test_the_reservation_counts_a_json_schema_like_any_other_text() -> None:
    canonical = _with_json_schemas()

    texts = list(prompt_texts(canonical))

    assert texts.count(json.dumps(JSON_SCHEMA, separators=(",", ":"))) == 2


# ---- FR-4: through the surface, end to end -------------------------------------------------------


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0))


async def _declare(app: Any, **fields: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="mock-1", **fields))
        await session.commit()


async def test_a_json_schema_tool_is_called_through_the_surface() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, capabilities=["generate", "tools"])
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
            await session.commit()
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            headers={"x-aira-use-case": "uc"},
            json={
                "contents": USER,
                "tools": _tools({"name": "read_file", "parametersJsonSchema": JSON_SCHEMA}),
                "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            },
        )

    assert response.status_code == 200, response.text
    call = response.json()["candidates"][0]["content"]["parts"][0]["functionCall"]
    assert call["name"] == "read_file"
    assert set(call["args"]) == {"path", "limit"}


async def test_a_json_schema_answer_comes_back_as_a_document() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, capabilities=["generate", "structured_output"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                "contents": USER,
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": JSON_SCHEMA,
                },
            },
        )

    assert response.status_code == 200, response.text
    document = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
    assert set(document) == {"path", "limit"}


async def test_a_model_without_structured_output_refuses_a_json_schema_answer_too() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(app, capabilities=["generate"])
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                "contents": USER,
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": JSON_SCHEMA,
                },
            },
        )

    assert response.status_code == 400
    assert "structured output" in response.json()["error"]["message"]


# ---- FR-6: an organisation policy that forbids a partner feature, named --------------------------


def test_a_forbidden_partner_feature_is_explained() -> None:
    found = from_body({"error": {"code": 400, "status": "FAILED_PRECONDITION"}}, 400)

    assert found is not None
    assert "constraints/vertexai.allowedPartnerModelFeatures" in found.explanation


def test_an_anthropic_tool_declared_in_json_schema_reaches_the_canonical_core() -> None:
    """The canonical type, built directly: what an adapter is handed."""
    tool = ToolDeclaration(name="t", parameters_json_schema=parse_json_schema(JSON_SCHEMA))
    request = CanonicalRequest(
        model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")], tools=(tool,)
    )

    assert canonical_to_anthropic(request, max_tokens=64)["tools"][0]["input_schema"] == JSON_SCHEMA


# ---- FR-5: Google Cloud's own identity, when nothing else is configured --------------------------


def test_with_nothing_configured_on_google_cloud_the_machine_is_the_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aira_gateway.upstreams.vertex as vertex

    monkeypatch.setattr(vertex, "on_google_cloud", lambda: True)

    upstreams = vertex.build_vertex_upstreams(GatewaySettings(vertex_project="p"))

    assert upstreams, "the platform is registered"
    assert all(upstream._transport.tokens is not None for upstream in upstreams)


def test_a_configured_credential_is_never_traded_for_the_machines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import aira_gateway.upstreams.vertex as vertex
    from aira_gateway.upstreams.vertex.auth import CredentialsInvalid

    asked: list[bool] = []
    monkeypatch.setattr(vertex, "on_google_cloud", lambda: asked.append(True) or True)

    with pytest.raises(CredentialsInvalid, match="Mount the file at that path, or unset"):
        vertex.build_vertex_upstreams(
            GatewaySettings(vertex_project="p", vertex_credentials_file=str(tmp_path / "key.json"))
        )
    assert asked == [], "a configured credential means the machine is not asked"


def test_off_google_cloud_nothing_configured_registers_nothing() -> None:
    import aira_gateway.upstreams.vertex as vertex

    assert vertex.build_vertex_upstreams(GatewaySettings(vertex_project="p")) == []


def _probing(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    from aira_gateway.upstreams.vertex import credentials

    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs.get("trust_env") is False, "the metadata server is never asked via a proxy"
        return real(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(credentials.httpx, "Client", client)


@pytest.mark.real_metadata_probe
@pytest.mark.parametrize(
    ("answer", "on_gcp"),
    [
        (lambda request: httpx.Response(200, headers={"Metadata-Flavor": "Google"}), True),
        (lambda request: httpx.Response(200), False),
        (lambda request: httpx.Response(404, headers={"Metadata-Flavor": "Google"}), False),
    ],
)
def test_google_cloud_is_recognised_by_its_metadata_server(
    monkeypatch: pytest.MonkeyPatch, answer: Any, on_gcp: bool
) -> None:
    from aira_gateway.upstreams.vertex.credentials import on_google_cloud

    _probing(monkeypatch, answer)

    assert on_google_cloud() is on_gcp


@pytest.mark.real_metadata_probe
def test_no_metadata_server_is_not_google_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    from aira_gateway.upstreams.vertex.credentials import on_google_cloud

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such host", request=request)

    _probing(monkeypatch, unreachable)

    assert on_google_cloud() is False


def test_a_single_tool_object_is_a_list_of_one() -> None:
    """What the AI SDK's Google provider sent in its first major, and older agents still run."""
    canonical = _canonical(
        tools={"functionDeclarations": [{"name": "read_file", "parameters": OPENAPI}]}
    )

    assert [tool.name for tool in canonical.tools] == ["read_file"]
