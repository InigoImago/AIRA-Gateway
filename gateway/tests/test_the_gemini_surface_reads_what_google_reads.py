"""The Gemini surface reads every spelling Google's own parser reads (`FRD-155`).

Measured, not assumed: thirty-odd Gemini clients in seven languages were pointed at this gateway
with nothing but their base URL and key changed. The ones that failed did not send anything Google
refuses — they sent the request in another of the spellings proto3's JSON mapping allows, and this
surface read only one of them:

- a field by its proto name in snake_case — LiteLLM (`function_declarations`, `response_mime_type`),
  and `google-genai` inside a function declaration;
- an enum by its number — every GAPIC client: the deprecated Python and Go SDKs, LangChainGo;
- a field at its default — LangChain (Python and JavaScript) and Google's own deprecated JavaScript
  SDK send `safetySettings: []` on every call, the PHP client `null` for every field it has;
- one object where a list is expected — the `gemini-ai` Ruby gem;
- the model's name in the body as well as the URL — the Go SDK;
- `/v1` instead of `/v1beta` — the default of Google's JavaScript and Go SDKs;
- what a function returns (`response`, `responseJsonSchema`) — every current `google-genai` SDK
  running a typed function itself; and a document's `title` on an embedding.

Each is the same request as the one the surface already served, so each is read as it — and every
refusal by design still holds, under either spelling and only where something is asked for.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import gemini_to_canonical, gemini_to_embedding
from aira_gateway.api.serving import prepare
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    MAX_MESSAGES,
    MAX_TOOLS,
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    TextPart,
    ToolDeclaration,
)
from aira_gateway.core.json_schema import parse_json_schema
from aira_gateway.core.schema import SchemaRejected, SchemaType
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.exception_handlers import NOT_VERTEX
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    batch_embedding_body,
    canonical_to_gemini_embedding,
    canonical_to_gemini_request,
    count_tokens_body,
)
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai.mapping import canonical_to_openai
from aira_gateway.upstreams.openai.mapping.request import canonical_to_openai_embedding
from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic
from aira_gateway.upstreams.vertex.embedding_mapping import embed_content_body, predict_body

MODEL = "mock-1"
GENERATE = f"/v1beta/models/{MODEL}:generateContent"
TEXT = {"role": "user", "parts": [{"text": "hallo"}]}


def _declaration() -> ModelRead:
    """The mock's model, declared to embed as a well-configured installation declares one."""
    return ModelRead(
        model=MODEL,
        approved=True,
        capabilities=["generate", "embed", "tools", "structured_output", "speech"],
        embedding={
            "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"],
            "supports_batch": True,
            "dimensions": [8],
        },
    )


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0))


async def _declared(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(_declaration())
        await session.commit()


#: The use case a function is declared in: tool calling is the use case's to switch on (`FRD-131`).
USE_CASE = {"x-aira-use-case": "uc"}


async def _with_tools(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(_declaration())
        session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True, allowed_models=[MODEL]))
        await session.commit()


def _canonical(body: dict[str, Any]) -> CanonicalRequest:
    return gemini_to_canonical(MODEL, schemas.GenerateContentRequest.model_validate(body))


def _error(response: Any) -> str:
    return str(response.json()["error"]["message"])


# == a field by its proto name =====================================================================

CAMEL = {
    "systemInstruction": {"parts": [{"text": "Antworte knapp."}]},
    "contents": [TEXT],
    "generationConfig": {
        "maxOutputTokens": 64,
        "responseMimeType": "application/json",
        "responseSchema": {"type": "OBJECT", "properties": {"city": {"type": "STRING"}}},
        "stopSequences": ["ENDE"],
        "topP": 0.5,
    },
    "tools": [
        {
            "functionDeclarations": [
                {"name": "lookup", "parametersJsonSchema": {"type": "object"}},
            ]
        }
    ],
    "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["lookup"]}},
}

SNAKE = {
    "system_instruction": {"parts": [{"text": "Antworte knapp."}]},
    "contents": [TEXT],
    "generation_config": {
        "max_output_tokens": 64,
        "response_mime_type": "application/json",
        "response_schema": {"type": "OBJECT", "properties": {"city": {"type": "STRING"}}},
        "stop_sequences": ["ENDE"],
        "top_p": 0.5,
    },
    "tools": [
        {
            "function_declarations": [
                {"name": "lookup", "parameters_json_schema": {"type": "object"}},
            ]
        }
    ],
    "tool_config": {
        "function_calling_config": {"mode": "ANY", "allowed_function_names": ["lookup"]}
    },
}


def test_a_request_in_proto_field_names_is_the_same_request() -> None:
    """LiteLLM writes `function_declarations` and `response_mime_type`, as Google's parser allows.
    Read the other way round it is the same request, and it must arrive as the same one — not as
    a camelCase request with its snake_case half quietly missing."""
    assert _canonical(SNAKE) == _canonical(CAMEL)


async def test_litellms_own_bodies_are_served() -> None:
    """The two bodies LiteLLM sent that were refused: a tool, and a structured answer."""
    tool = {
        "contents": [TEXT],
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "get_weather",
                        "description": "Returns the weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    }
                ]
            }
        ],
        "generationConfig": {},
    }
    structured = {
        "contents": [TEXT],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    }
    app = _app()
    with TestClient(app) as client:
        await _with_tools(app)
        called = client.post(GENERATE, json=tool, headers=USE_CASE)
        answered = client.post(GENERATE, json=structured, headers=USE_CASE)

    assert called.status_code == 200, called.text
    assert called.json()["candidates"][0]["content"]["parts"][0]["functionCall"]["name"] == (
        "get_weather"
    )
    assert answered.status_code == 200, answered.text


@pytest.mark.parametrize("config", ["generationConfig", "generation_config"])
def test_a_refusal_names_the_field_as_google_documents_it(config: str) -> None:
    """Accepting both spellings must not make an error speak snake_case: whichever the client
    wrote, the refusal names the field as Google's reference does."""
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json={"contents": [TEXT], config: {"x": 1}})

    assert response.status_code == 400
    assert "generationConfig.x" in _error(response)


# == an enum by its number =========================================================================


def test_a_schema_type_by_number_is_the_type() -> None:
    """The deprecated Python SDK sends `{"type": 6}` for an object and `1` for a string."""
    by_number = parse_schema({"type": 6, "properties": {"city": {"type": 1}, "n": {"type": 3}}})
    by_name = parse_schema(
        {"type": "OBJECT", "properties": {"city": {"type": "STRING"}, "n": {"type": "INTEGER"}}}
    )

    assert by_number == by_name
    assert parse_schema({"type": 7, "properties": None}).type is SchemaType.NULL


def test_an_unspecified_type_is_no_type() -> None:
    """``0`` is `TYPE_UNSPECIFIED`, which only a union may leave out — as by name."""
    union = parse_schema({"type": 0, "anyOf": [{"type": 1}, {"type": 7}]})
    assert union.type is None
    with pytest.raises(SchemaRejected, match="only an 'anyOf'"):
        parse_schema({"type": "TYPE_UNSPECIFIED"})


def test_a_legacy_sdks_tool_and_task_by_number_are_served() -> None:
    """Every shape the deprecated SDK was refused for, in the numbers it sends them in."""
    tool = {
        "contents": [TEXT],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "get_weather",
                        "parameters": {"type": 6, "properties": {"city": {"type": 1}}},
                    }
                ]
            }
        ],
        "toolConfig": {"functionCallingConfig": {"mode": 2}},
    }
    embedding = {"content": {"parts": [{"text": "hallo"}]}, "taskType": 1}
    batch = {
        "requests": [
            {"model": f"models/{MODEL}", "content": {"parts": [{"text": t}]}, "taskType": 2}
            for t in ("eins", "zwei")
        ]
    }

    assert _canonical(tool).tool_choice is not None
    assert _canonical(tool).tool_choice.mode == "any"  # type: ignore[union-attr]
    assert schemas.EmbedContentRequest.model_validate(embedding).taskType == "RETRIEVAL_QUERY"
    requests = schemas.BatchEmbedContentsRequest.model_validate(batch).requests
    assert [entry.taskType for entry in requests] == ["RETRIEVAL_DOCUMENT"] * 2


@pytest.mark.parametrize(
    ("number", "name"),
    [(1, "RETRIEVAL_QUERY"), (2, "RETRIEVAL_DOCUMENT"), (8, "CODE_RETRIEVAL_QUERY")],
)
def test_task_types_carry_googles_numbers(number: int, name: str) -> None:
    entry = {"content": {"parts": [{"text": "a"}]}, "task_type": number}
    assert schemas.EmbedContentRequest.model_validate(entry).taskType == name


def test_speech_by_modality_number_is_speech() -> None:
    body = {
        "contents": [TEXT],
        "generationConfig": {
            "responseModalities": [3],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
        },
    }
    assert _canonical(body).speech is not None


# == a field at its default is a field not sent ====================================================


@pytest.mark.parametrize(
    "body",
    [
        # LangChain (both languages) and the deprecated JavaScript SDK, on every call.
        {"contents": [TEXT], "safetySettings": []},
        # The PHP client: every field it has, `null` where it has nothing to say.
        {
            "contents": [TEXT],
            "tools": [],
            "toolConfig": None,
            "safetySettings": [],
            "systemInstruction": None,
            "generationConfig": None,
            "cachedContent": None,
        },
        # LangChainGo with its "unspecified" threshold: one entry per category, no threshold.
        {"contents": [TEXT], "safetySettings": [{"category": 10}, {"category": 7}]},
        {
            "contents": [TEXT],
            "safety_settings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": 0},
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "HARM_BLOCK_THRESHOLD_UNSPECIFIED",
                },
            ],
        },
        {"contents": [TEXT], "cachedContent": ""},
        {
            "contents": [TEXT],
            "generationConfig": {
                "responseLogprobs": False,
                "logprobs": 0,
                "enableEnhancedCivicAnswers": False,
                "mediaResolution": "MEDIA_RESOLUTION_UNSPECIFIED",
                "candidateCount": 0,
                "responseMimeType": "",
            },
        },
        {"contents": [{"role": "", "parts": [{"text": "hallo", "fileData": None}]}]},
    ],
    ids=[
        "empty-safety",
        "php-nulls",
        "no-thresholds",
        "unspecified-thresholds",
        "empty-cache",
        "config-defaults",
        "empty-role-null-part",
    ],
)
def test_a_field_at_its_default_asks_for_nothing(body: dict[str, Any]) -> None:
    """Google reads `[]`, `null`, `""`, `false`, `0` and `…_UNSPECIFIED` as a field not sent.
    LangChain's `safetySettings: []` alone made every LangChain call through this gateway a 400,
    in Python and in JavaScript, though it asks for no setting at all."""
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("value", "is_unset"),
    [
        (None, True),
        ([], True),
        ("", True),
        (False, True),
        (0, True),
        ("HARM_BLOCK_THRESHOLD_UNSPECIFIED", True),
        ({}, False),
        ([{}], False),
        ("BLOCK_NONE", False),
        (3, False),
        (True, False),
    ],
)
def test_proto3s_not_set_is_googles(value: Any, is_unset: bool) -> None:
    """What every refusal by design and every default above is decided by: a repeated field left
    empty is a field not sent, and an object, however empty, is present."""
    assert schemas.unset(value) is is_unset


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ({"contents": [TEXT], "cached_content": "cachedContents/abc"}, "cachedContent"),
        (
            {"contents": [TEXT], "generation_config": {"response_logprobs": True}},
            "responseLogprobs",
        ),
        ({"contents": [TEXT], "generationConfig": {"logprobs": 3}}, "logprobs"),
        ({"contents": [TEXT], "tools": [{"codeExecution": {}}]}, "codeExecution"),
        ({"contents": [TEXT], "tools": [{"google_search": {}}]}, "googleSearch"),
        (
            {"contents": [{"parts": [{"file_data": {"fileUri": "gs://x", "mimeType": "a/b"}}]}]},
            "fileData",
        ),
        (
            {
                "contents": [TEXT],
                "tools": [{"functionDeclarations": [{"name": "f", "behavior": 2}]}],
            },
            "NON_BLOCKING",
        ),
    ],
    ids=[
        "snake-cache",
        "snake-logprobs",
        "logprobs",
        "code-execution",
        "google-search",
        "snake-file",
        "non-blocking",
    ],
)
def test_what_asks_for_something_refused_is_still_refused_by_name(
    body: dict[str, Any], named: str
) -> None:
    """The refusals by design hold in either spelling, and an empty object is no exception: its
    presence is what switches a tool on."""
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 400, response.text
    assert named in _error(response)


# == harm filters ==================================================================================


def _filtered(**fields: Any) -> CanonicalRequest:
    return _canonical({"contents": [TEXT], **fields})


@pytest.mark.parametrize(
    "fields",
    [
        {"safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}]},
        {"safety_settings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}]},
        # A GAPIC client's numbers: HARASSMENT is 7 and BLOCK_NONE 4 in Google's enums.
        {"safetySettings": [{"category": 7, "threshold": 4}]},
        {"safetySettings": {"category": "harm_category_harassment", "threshold": "block_none"}},
    ],
    ids=["camel", "snake", "numbers", "one-object-lowercase"],
)
def test_a_harm_filter_reaches_gemini_in_googles_names(fields: dict[str, Any]) -> None:
    """Carried as Google names them, however the client spelled them."""
    body = canonical_to_gemini_request(_filtered(**fields))

    assert body["safetySettings"] == [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
    ]


def test_an_entry_naming_no_threshold_is_left_out_and_the_rest_carried() -> None:
    """LangChainGo sends one entry per category with no threshold: each asks for the model's
    default, which is no entry at all — and one beside them that names a threshold is carried."""
    request = _filtered(
        safetySettings=[
            {"category": 10},
            {"category": 7, "threshold": 0},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"},
        ]
    )

    assert canonical_to_gemini_request(request)["safetySettings"] == [
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"}
    ]
    assert "safetySettings" not in canonical_to_gemini_request(
        _filtered(safetySettings=[{"category": 10}])
    )


@pytest.mark.parametrize(
    ("setting", "named"),
    [
        # A PaLM model's category, by number: Google refuses it on a Gemini request.
        ({"category": 2, "threshold": 1}, "HARM_CATEGORY_TOXICITY"),
        # Vertex AI's own category, which the Gemini API does not have.
        ({"category": "HARM_CATEGORY_JAILBREAK", "threshold": "OFF"}, "HARM_CATEGORY_JAILBREAK"),
        (
            {"category": "HARM_CATEGORY_UNSPECIFIED", "threshold": "OFF"},
            "HARM_CATEGORY_UNSPECIFIED",
        ),
        ({"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_SOME"}, "BLOCK_SOME"),
        ({"category": "HARM_CATEGORY_HARASSMENT", "threshold": 9}, "block threshold"),
        ({"threshold": "OFF"}, "category"),
        # Vertex AI's, too: the Gemini API's setting has two fields.
        ({"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF", "method": 1}, "method"),
    ],
    ids=["palm", "vertex-only", "unspecified", "no-such-word", "no-such-number", "none", "method"],
)
def test_a_filter_google_does_not_take_is_refused_naming_it(
    setting: dict[str, Any], named: str
) -> None:
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json={"contents": [TEXT], "safetySettings": [setting]})

    assert response.status_code == 400, response.text
    assert named in _error(response)
    assert "safetySettings" in _error(response)


def test_the_refusal_lists_what_google_takes() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            GENERATE,
            json={"contents": [TEXT], "safetySettings": [{"category": 1, "threshold": 1}]},
        )

    assert all(category in _error(response) for category in schemas.GEMINI_HARM_CATEGORIES)


@pytest.mark.parametrize(
    "dialect",
    [canonical_to_openai, lambda request: canonical_to_anthropic(request, max_tokens=64)],
    ids=["openai", "anthropic"],
)
def test_a_dialect_without_harm_filters_passes_the_candidate_over(dialect: Any) -> None:
    """Claude and an OpenAI-compatible server have no such filter: answered without it, the model
    would let through what the caller asked to be blocked, under a 200."""
    request = _filtered(
        safetySettings=[{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"}]
    )

    with pytest.raises(DialectUnsupported, match="'safetySettings'") as refused:
        dialect(request)
    assert "HARM_CATEGORY_HARASSMENT at BLOCK_ONLY_HIGH" in str(refused.value)


@pytest.mark.parametrize(
    "dialect",
    [canonical_to_openai, lambda request: canonical_to_anthropic(request, max_tokens=64)],
    ids=["openai", "anthropic"],
)
def test_a_request_without_filters_still_reaches_every_dialect(dialect: Any) -> None:
    assert dialect(_filtered(safetySettings=[{"category": 10}])) is not None


def test_a_filtered_request_is_served() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            GENERATE,
            json={
                "contents": [TEXT],
                "safetySettings": [
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
                ],
            },
        )

    assert response.status_code == 200, response.text


class _OpenAIDialect:
    """A provider speaking the OpenAI dialect through its real mapper, recording what it sent."""

    is_test_double = True
    sampling_controls = frozenset({"top_p"})

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[dict[str, Any]] = []

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                name=self.name,
                version="1",
                supported_methods=("generateContent",),
                provider="test",
                region="",
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.sent.append(canonical_to_openai(request))
        return CanonicalResponse(
            model=request.model,
            text="unfiltered",
            finish_reason="stop",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


FILTERED = {
    "contents": [TEXT],
    "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"}],
}


async def test_a_chain_passes_a_candidate_without_the_filter_over_by_name() -> None:
    """The answer comes from the model that applies the filter, and the row says who was passed
    over and why."""
    openai = _OpenAIDialect("openai-1")
    app = _app()
    app.state.providers = ProviderRegistry([openai, MockProvider(MODEL)])
    with TestClient(app) as client:

        class _Store:
            async def get(self, use_case: Any) -> Pipeline:
                return Pipeline(steps=(), fallback_models=(MODEL,))

        app.state.pipeline_store = _Store()
        response = client.post("/v1beta/models/openai-1:generateContent", json=FILTERED)
        async with app.state.db_sessionmaker() as session:
            (row,) = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 200, response.text
    assert openai.sent == [], "the candidate without the filter was sent the request"
    assert row.model == MODEL
    assert "safetySettings" in json.dumps(row.pipeline_decisions)


async def test_a_model_without_the_filter_and_nothing_behind_it_refuses_by_name() -> None:
    openai = _OpenAIDialect("openai-1")
    app = _app()
    app.state.providers = ProviderRegistry([openai])
    with TestClient(app) as client:
        refused = client.post("/v1beta/models/openai-1:generateContent", json=FILTERED)
        served = client.post("/v1beta/models/openai-1:generateContent", json={"contents": [TEXT]})

    assert refused.status_code == 400, refused.text
    assert "'safetySettings' sets Google's own harm filters" in _error(refused)
    assert served.status_code == 200, "a request without a filter still reaches the model"
    assert len(openai.sent) == 1


def test_a_filter_is_not_counted_as_input() -> None:
    """`:countTokens` counts what a generation is billed for as input; a filter is not input."""
    body = canonical_to_gemini_request(_filtered(safetySettings=[{"category": 7, "threshold": 4}]))

    assert "safetySettings" not in count_tokens_body(body)


# == the bounds hold in either spelling ============================================================


def test_declarations_in_snake_case_are_counted_before_validation() -> None:
    """The bound on functions is counted on the raw body, before anything is validated. A count
    that knew only `functionDeclarations` would let `function_declarations` past it."""
    declarations = [{"name": f"f{i}"} for i in range(MAX_TOOLS + 1)]
    body = {"contents": [TEXT], "tools": [{"function_declarations": declarations}]}
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 400
    assert str(MAX_TOOLS) in _error(response)


def test_a_snake_case_instruction_is_counted_as_a_turn() -> None:
    body = {"contents": [TEXT] * MAX_MESSAGES, "system_instruction": {"parts": [{"text": "s"}]}}
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 400, response.text


def test_one_turn_sent_as_an_object_is_counted_with_its_parts() -> None:
    """One object is a list of one for the count as for validation, so no part escapes it."""
    body = {"contents": {"parts": [{"text": "x"}] * 20_001}}
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 400
    assert "20000" in _error(response).replace(" ", "").replace(",", "")


# == one object where a list is expected ===========================================================


def test_one_turn_and_one_part_as_objects_are_served() -> None:
    """The `gemini-ai` gem's own example: `contents` and `parts` as objects."""
    body = {"contents": {"role": "user", "parts": {"text": "hallo"}}}
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json=body)

    assert response.status_code == 200, response.text
    assert _canonical(body) == _canonical({"contents": [TEXT]})


# == the model in the body =========================================================================


def test_a_body_that_repeats_its_model_is_served() -> None:
    """The Go SDK puts `model` in the body as well as in the URL."""
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json={"model": f"models/{MODEL}", "contents": [TEXT]})

    assert response.status_code == 200, response.text


def test_a_body_naming_another_model_is_refused() -> None:
    """The URL decides: an answer from the model the URL named, under a body that named another,
    would be the other model's answer to a caller who believes it got this one."""
    with TestClient(_app()) as client:
        response = client.post(GENERATE, json={"model": "models/other", "contents": [TEXT]})

    assert response.status_code == 400
    assert "'models/other'" in _error(response)


# == /v1 ===========================================================================================


def test_the_surface_answers_at_v1_as_at_v1beta() -> None:
    """Google's JavaScript and Go SDKs call `/v1` unless told otherwise: with only the base URL
    changed, every call they made was a 404. One handler serves both, so the answers agree."""
    body = {"contents": [TEXT]}
    with TestClient(_app()) as client:
        beta = client.post(GENERATE, json=body)
        stable = client.post(f"/v1/models/{MODEL}:generateContent", json=body)
        listed = client.get("/v1/models")
        one = client.get(f"/v1/models/{MODEL}")
        streamed = client.post(f"/v1/models/{MODEL}:streamGenerateContent?alt=sse", json=body)

    assert stable.status_code == 200, stable.text
    assert stable.json() == beta.json()
    assert listed.status_code == 200
    assert any(model["name"] == f"models/{MODEL}" for model in listed.json()["models"])
    assert one.status_code == 200
    assert streamed.status_code == 200
    assert "data: " in streamed.text


async def test_embedding_answers_at_v1() -> None:
    app = _app()
    body = {"requests": [{"model": f"models/{MODEL}", "content": {"parts": [{"text": "a"}]}}]}
    with TestClient(app) as client:
        await _declared(app)
        response = client.post(f"/v1/models/{MODEL}:batchEmbedContents", json=body)

    assert response.status_code == 200, response.text
    assert len(response.json()["embeddings"]) == 1


@pytest.mark.parametrize("path", ["/v1/nothing-here", "/v1beta/nothing-here", "/v1beta1/models"])
def test_a_wrong_url_where_googles_clients_call_is_answered_in_googles_envelope(path: str) -> None:
    """The framework's 404 carried `code` as a word: the Go SDK reported it as raw JSON, and a
    client that reads `error.code` as a number failed to parse the refusal at all."""
    with TestClient(_app()) as client:
        response = client.get(path)

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == 404
    assert error["status"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path",
    [
        f"/v1beta1/projects/p/locations/europe-west1/publishers/google/models/{MODEL}:generateContent",
        f"/v1/projects/p/locations/global/publishers/google/models/{MODEL}:generateContent",
        f"/v1beta1/publishers/google/models/{MODEL}:generateContent",
    ],
)
def test_a_client_in_vertex_mode_is_told_which_api_this_is(path: str) -> None:
    """A client in Vertex mode builds Vertex's paths, which this gateway does not serve. A bare
    "Not found" leaves its developer looking for a typo; the answer says what to configure."""
    with TestClient(_app()) as client:
        response = client.post(path, json={"contents": [TEXT]})

    assert response.status_code == 404
    assert response.json()["error"]["message"] == NOT_VERTEX


# == ranking =======================================================================================

RANK_PATH = "/v1/projects/p/locations/global/rankingConfigs/default_ranking_config:rank"


async def _ranker(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=MODEL, approved=True, capabilities=["rerank"]))
        await session.commit()


def _ranking(**extra: Any) -> dict[str, Any]:
    return {
        "model": MODEL,
        "query": "rote katze",
        "records": [{"id": "1", "content": "ein hund"}, {"id": "2", "content": "eine rote katze"}],
        **extra,
    }


@pytest.mark.parametrize(
    "extra",
    [
        # The Go client repeats the path's configuration in the body.
        {"rankingConfig": "projects/p/locations/global/rankingConfigs/default_ranking_config"},
        # The Node client sends empty labels on every request.
        {"userLabels": {}},
        {"top_n": 1, "ignore_record_details_in_response": True},
        {"topN": 0},
    ],
    ids=["ranking-config", "empty-labels", "snake-case", "no-limit"],
)
async def test_googles_ranking_clients_bodies_are_served(extra: dict[str, Any]) -> None:
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(RANK_PATH, json=_ranking(**extra))

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert records[0]["id"] == "2"
    if extra.get("top_n"):
        assert len(records) == 1
        assert "content" not in records[0]
    if "topN" in extra:
        assert len(records) == 2


async def test_labels_on_a_ranking_are_refused_by_name() -> None:
    """Labels sort the caller's own Google bill; this gateway ranks in its own project, so they
    would label nothing — refused rather than taken and dropped."""
    app = _app()
    with TestClient(app) as client:
        await _ranker(app)
        response = client.post(RANK_PATH, json=_ranking(userLabels={"team": "search"}))

    assert response.status_code == 400
    assert "userLabels" in _error(response)


# == what a function returns =======================================================================

#: What `google-genai` (Python, 2.x) sends for `def get_weather(city: str) -> str` when it runs the
#: function itself: snake_case inside the declaration, and the result's schema beside the
#: parameters'.
AFC_BODY: dict[str, Any] = {
    "contents": [TEXT],
    "tools": [
        {
            "functionDeclarations": [
                {
                    "description": "Returns the weather for a city.",
                    "name": "get_weather",
                    "parameters_json_schema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "response_json_schema": {"type": "string"},
                }
            ]
        }
    ],
    "generationConfig": {},
}

#: What the Java SDK sends for the same function: Google's OpenAPI subset, with a `title`.
JAVA_AFC_TOOL: dict[str, Any] = {
    "functionDeclarations": [
        {
            "description": "",
            "name": "getWeather",
            "parameters": {
                "properties": {"city": {"title": "city", "type": "STRING"}},
                "required": ["city"],
                "type": "OBJECT",
            },
            "response": {"title": "return type", "type": "STRING"},
        }
    ]
}


async def test_a_function_that_says_what_it_returns_is_served() -> None:
    """Automatic function calling in the current Python and Java SDKs: refused on every call."""
    app = _app()
    with TestClient(app) as client:
        await _with_tools(app)
        python = client.post(GENERATE, json=AFC_BODY, headers=USE_CASE)
        java = client.post(
            GENERATE, json={"contents": [TEXT], "tools": [JAVA_AFC_TOOL]}, headers=USE_CASE
        )

    for response in (python, java):
        assert response.status_code == 200, response.text
        assert "functionCall" in response.json()["candidates"][0]["content"]["parts"][0]


def test_gemini_is_told_what_the_function_returns() -> None:
    """Carried in the vocabulary it was written in, as the parameters are."""
    python = canonical_to_gemini_request(_canonical(AFC_BODY))
    java = canonical_to_gemini_request(_canonical({"contents": [TEXT], "tools": [JAVA_AFC_TOOL]}))

    (declared,) = python["tools"][0]["functionDeclarations"]
    assert declared["responseJsonSchema"] == {"type": "string"}
    (declared,) = java["tools"][0]["functionDeclarations"]
    assert declared["response"]["type"] == "STRING"


def _declaring(**field: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model=MODEL,
        messages=[CanonicalMessage(role=Role.USER, parts=[TextPart(text="hallo")])],
        tools=(ToolDeclaration(name="get_weather", **field),),
    )


@pytest.mark.parametrize(
    "field",
    [
        {"response_json_schema": parse_json_schema({"type": "string"}, None, what="r")},
        {"response": parse_schema({"type": "STRING"})},
    ],
    ids=["json-schema", "openapi"],
)
@pytest.mark.parametrize(
    "dialect",
    [canonical_to_openai, lambda request: canonical_to_anthropic(request, max_tokens=64)],
    ids=["openai", "anthropic"],
)
def test_a_dialect_without_the_word_passes_the_candidate_over(field: Any, dialect: Any) -> None:
    """Claude's tools and OpenAI's functions have no result schema: the candidate is passed over
    by name, and a Gemini model on the chain takes the request as written."""
    with pytest.raises(DialectUnsupported, match="'get_weather' declares what it returns"):
        dialect(_declaring(**field))


async def test_declaring_the_result_twice_is_refused() -> None:
    tool = {
        "functionDeclarations": [
            {"name": "f", "response": {"type": "STRING"}, "responseJsonSchema": {"type": "string"}}
        ]
    }
    app = _app()
    with TestClient(app) as client:
        await _with_tools(app)
        response = client.post(
            GENERATE, json={"contents": [TEXT], "tools": [tool]}, headers=USE_CASE
        )

    assert response.status_code == 400
    assert "returns twice" in _error(response)


# == a document's title ============================================================================


def _titled(*titles: str) -> CanonicalEmbeddingRequest:
    return CanonicalEmbeddingRequest(
        model=MODEL,
        texts=[f"text {i}" for i in range(len(titles))],
        task_type="RETRIEVAL_DOCUMENT",
        titles=list(titles),
    )


def test_a_title_reaches_every_google_embedding_body() -> None:
    """Each in the field Google's own SDK writes it in: an entry's `title` on the Gemini API, an
    instance's on Vertex's `:predict`, the config's on its `:embedContent`."""
    one = _titled("Zugspitze")
    two = _titled("Zugspitze", "")

    assert canonical_to_gemini_embedding(one)["title"] == "Zugspitze"
    entries = batch_embedding_body(two, MODEL)["requests"]
    assert entries[0]["title"] == "Zugspitze"
    assert "title" not in entries[1]
    assert predict_body(one, "text 0", "Zugspitze")["instances"][0]["title"] == "Zugspitze"
    assert "title" not in predict_body(one, "text 0")["instances"][0]
    config = embed_content_body(one, ["text 0"], "Zugspitze")["embedContentConfig"]
    assert config["title"] == "Zugspitze"


def test_a_dialect_without_titles_passes_a_titled_request_over() -> None:
    with pytest.raises(DialectUnsupported, match="title"):
        canonical_to_openai_embedding(_titled("Zugspitze"))
    # Untitled, it is embedded as ever.
    assert canonical_to_openai_embedding(_titled(""))["input"] == ["text 0"]


async def test_a_title_counts_towards_what_one_embedding_may_carry() -> None:
    """The bound on characters is what a batch costs the pipeline and the provider; a title is
    text the request carries, so a long one is no way around it."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, max_embedding_chars=40))
    entry = {"content": {"parts": [{"text": "kurz"}]}, "taskType": "RETRIEVAL_DOCUMENT"}
    path = f"/v1beta/models/{MODEL}:batchEmbedContents"
    with TestClient(app) as client:
        await _declared(app)
        short = client.post(path, json={"requests": [{**entry, "title": "Titel"}]})
        long = client.post(path, json={"requests": [{**entry, "title": "T" * 40}]})

    assert short.status_code == 200, short.text
    assert long.status_code == 400
    assert "40" in _error(long)


def test_titles_are_read_per_entry() -> None:
    entries = [
        schemas.EmbedContentRequest.model_validate(
            {"content": {"parts": [{"text": t}]}, "title": title}
        )
        for t, title in (("a", "Erster"), ("b", None), ("c", ""))
    ]
    request = gemini_to_embedding(MODEL, entries)
    assert request.titles == ["Erster", "", ""]
    assert gemini_to_embedding(MODEL, entries[1:]).titles == []


async def test_a_title_changes_the_vector_and_none_does_not() -> None:
    """Through the surface to the mock, which folds the title in as Google's model does."""
    app = _app()

    def body(title: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"content": {"parts": [{"text": "Die Zugspitze ist 2962 m hoch."}]}}
        if title is not ...:
            entry["title"] = title
        return {"requests": [{**entry, "taskType": "RETRIEVAL_DOCUMENT"}]}

    path = f"/v1beta/models/{MODEL}:batchEmbedContents"
    with TestClient(app) as client:
        await _declared(app)
        untitled = client.post(path, json=body(...))
        null = client.post(path, json=body(None))
        titled = client.post(path, json=body("Zugspitze"))

    for response in (untitled, null, titled):
        assert response.status_code == 200, response.text
    assert null.json() == untitled.json()
    assert titled.json() != untitled.json()


class _Redactor:
    """Embeds, and redacts the one name it knows wherever it is asked to."""

    is_test_double = True

    def __init__(self) -> None:
        self.embedded: list[CanonicalEmbeddingRequest] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(MODEL, MODEL, ("generateContent", "embedContent"))]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=MODEL,
            text=request.messages[-1].text.replace("Max Mustermann", "<PERSON>"),
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request: CanonicalRequest):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        self.embedded.append(request)
        return [[0.5] * 8 for _ in request.texts]


class _Store:
    async def get(self, use_case: str | None) -> Pipeline:
        step = PipelineStep(type=StepType.PII_FILTER, config={"model": MODEL})
        return Pipeline(steps=(step,), fallback_models=())


async def test_a_title_passes_the_use_cases_redactor_like_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title is the caller's text too. A redactor that saw only the texts would have sent the
    name in the title to the embedding model, and stored it — and the weight the rate limit is
    asked for counts what the redactor is asked to read."""
    weights: list[int] = []
    guard = prepare.guard_before_work

    async def weighed(request: Any, *, units: int) -> Any:
        weights.append(units)
        return await guard(request, units=units)

    monkeypatch.setattr(prepare, "guard_before_work", weighed)
    provider = _Redactor()
    app = _app()
    registry = ProviderRegistry([provider])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _Store()
    body = {
        "requests": [
            {
                "content": {"parts": [{"text": "Ein Brief."}]},
                "title": "An Max Mustermann",
                "taskType": "RETRIEVAL_DOCUMENT",
            },
            {"content": {"parts": [{"text": "Noch einer."}]}, "taskType": "RETRIEVAL_DOCUMENT"},
        ]
    }
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc", name="uc", allowed_models=[MODEL]))
            session.add(_declaration())
            await session.commit()
        response = client.post(
            f"/v1beta/models/{MODEL}:batchEmbedContents",
            json=body,
            headers={"x-aira-use-case": "uc"},
        )
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code == 200, response.text
    (sent,) = provider.embedded
    assert sent.titles == ["An <PERSON>", ""]
    stored = str([row.request_payload for row in rows if not row.operation.startswith("pipeline:")])
    assert "Mustermann" not in stored
    assert weights == [3], "two texts and one title: three texts for the redactor to read"


async def test_a_spoken_answer_still_arrives_as_audio() -> None:
    """The mock speaks; one end-to-end look that the speech path survived the new parsing."""
    body = {
        "contents": [{"parts": [{"text": "Guten Morgen"}]}],
        "generation_config": {
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}},
        },
    }
    app = _app()
    with TestClient(app) as client:
        await _declared(app)
        response = client.post(GENERATE, json=body)

    assert response.status_code == 200, response.text
    part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    assert base64.b64decode(part["data"])
