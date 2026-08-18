"""The predecessor's contract, served by AIRA (FRD-107, Stage A + B).

These are **contract tests**: they assert the response shape field by field against the
compatibility contract, because "compatible" has to be a fact rather than a claim. A migrating
consumer changes a base URL and nothing else, and the only thing that makes that true is checking
it.

Stage B turned the fields Stage A refused — thinking, `responseSchema`, batch embedding and task
types — into fields it serves, without touching the wire format. The second theme therefore
survives the change and only moves: **a field this gateway cannot honour is refused, never
ignored.** What changed is the reason. "Not built yet" is gone; "this model does not offer it" is
not, and it fails exactly as loudly, with the predecessor's own error codes.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog

BASE = "/kira/api/external"
PDF = b"%PDF-1.7\n" + b"x" * 200


def _app(**settings: Any):  # noqa: ANN201
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _catalogue(app, model: str = "mock-1", **fields: Any) -> None:  # noqa: ANN001
    defaults: dict[str, Any] = {
        "numeric_id": 1004,
        "capabilities": ["generate", "embed"],
        "publisher": "google",
    }
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, **{**defaults, **fields}))
        await session.commit()


def _chat(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "request": {"parts": [{"text": "Welche Zutaten brauche ich?"}]},
        "model_id": 1004,
    }
    body.update(over)
    return body


# == the contract ================================================================================


async def test_chat_answers_in_the_predecessors_shape() -> None:
    """The contract: ``parts`` plus ``usage_data`` with the two token counts."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"parts", "usage_data"}
    assert isinstance(payload["parts"], list)
    assert "text" in payload["parts"][0]
    assert set(payload["usage_data"]) == {"token_input", "token_output"}
    assert payload["usage_data"]["token_input"] > 0


async def test_the_camel_case_alias_is_accepted_as_the_predecessor_spells_it() -> None:
    """§12.1: `maxTokens` is the wire name. A surface that required the snake_case spelling would
    not be a compatibility surface."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, max_output_tokens=64000)
        response = client.post(f"{BASE}/chat", json=_chat(maxTokens=32))

    assert response.status_code == 200, response.text


async def test_the_system_prompt_and_history_reach_the_model_in_order() -> None:
    """History arrives oldest-first (§2.1). Reversing it would produce a coherent conversation
    about the wrong thing — fluent, and wrong in a way nothing in the response reveals."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"{BASE}/chat",
            json=_chat(
                system_instruction={"parts": [{"text": "be brief"}]},
                conversation_history=[
                    {"content": {"parts": [{"text": "first question"}]}, "role": "user"},
                    {"content": {"parts": [{"text": "first answer"}]}, "role": "model"},
                ],
            ),
        )

    assert response.status_code == 200, response.text
    # The mock echoes the last user text, which is the current turn rather than the history.
    assert "Zutaten" in response.json()["parts"][0]["text"]


async def test_an_unknown_model_id_is_422_model_not_found() -> None:
    """The contract's status, not ours.

    It was `404` and written down as a deliberate deviation — the *code* matched and the status did
    not — until the predecessor's own suite was run against this surface and the difference turned
    up as a failure a migrating client would also see. A generated HTTP client switches on the
    status before the body: `404` reads as "wrong URL" and `422` as "wrong field", and only the
    second sends anybody to look at `model_id`.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat(model_id=9999))

    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_NOT_FOUND"


async def test_a_model_the_gateway_does_not_serve_answers_the_same_way() -> None:
    """The other route to the same fact, which has to agree with the one above.

    The id resolves and the **name** turns out to be served by nothing — a catalogued model whose
    provider is gone. That refusal comes from the shared layer as a `404`, one step later than the
    id lookup, and answering it differently would make the status a caller sees depend on which of
    two equivalent failures happened to come first.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, model="not-served-by-anyone", numeric_id=4242)
        response = client.post(f"{BASE}/chat", json=_chat(model_id=4242))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "MODEL_NOT_FOUND"


async def test_the_error_envelope_is_the_predecessors() -> None:
    """§6.1: ``{code, message, details?}`` — not Gemini's ``{error: {...}}``. A client that
    cannot parse the error cannot handle it."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json={"model_id": 1004})

    assert response.status_code == 422
    body = response.json()
    assert set(body) >= {"code", "message"}
    assert "error" not in body
    assert body["code"] == "VALIDATION_ERROR"


async def test_a_body_that_is_not_json_says_so_in_the_predecessors_code() -> None:
    """**422, not 400, since 2026-08-12.** `400` is the better answer about HTTP — `422` means
    well-formed but wrong — and the predecessor answers `422` because malformed JSON reaches its
    validation handler. A compatibility surface copies the behaviour, not the better idea."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"{BASE}/chat", content=b"not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("payload", "code", "status"),
    [
        ({"maxTokens": -1}, "INVALID_MAX_TOKENS", 422),
        ({"maxTokens": 999_999}, "MAX_TOKENS_EXCEEDS_CAP", 422),
    ],
)
async def test_the_token_bounds_use_the_predecessors_codes(
    payload: dict[str, Any], code: str, status: int
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, max_output_tokens=64000)
        response = client.post(f"{BASE}/chat", json=_chat(**payload))

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_a_model_without_chat_capability_is_refused_with_its_own_code() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"])
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 422
    assert response.json()["code"] == "NO_CHAT_CAPABILITIES"


# == refused, never ignored ======================================================================


async def test_thinking_reaches_the_model_it_was_asked_for() -> None:
    """Stage B. The mock reports the setting it received, which is how a resolution that silently
    dropped the field would be caught rather than merely believed."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["medium", "auto"], "levels": {"medium": 2048}},
        )
        response = client.post(f"{BASE}/chat", json=_chat(thinking={"mode": "medium"}))

    assert response.status_code == 200, response.text
    # The level was translated to this model's own budget for it — the catalog's table, not a
    # constant in our code, which is what keeps a new model from being a code change.
    assert "thinking:medium budget=2048" in response.json()["parts"][0]["text"]


async def test_a_thinking_mode_the_model_does_not_offer_keeps_its_own_code() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "thinking"], thinking={"modes": ["auto"]})
        response = client.post(f"{BASE}/chat", json=_chat(thinking={"mode": "high"}))

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_THINKING_MODE"


@pytest.mark.parametrize(
    ("tokens", "code"),
    [
        (None, "MISSING_THINKING_TOKEN_COUNT"),
        (64, "THINKING_TOKEN_COUNT_TOO_LOW"),
        (99_999, "THINKING_TOKEN_COUNT_TOO_HIGH"),
    ],
)
async def test_each_budget_failure_has_its_own_code(tokens: int | None, code: str) -> None:
    """The contract gives these three separate codes, and the separation is the point: a
    client that cannot tell "you forgot the count" from "the count is too high" can fix neither."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            max_output_tokens=64_000,
            thinking={"modes": ["limited"], "min_tokens": 128, "max_tokens": 24_576},
        )
        setting: dict[str, Any] = {"mode": "limited"}
        if tokens is not None:
            setting["tokens"] = tokens
        response = client.post(f"{BASE}/chat", json=_chat(thinking=setting))

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_a_response_schema_produces_a_document_of_that_shape() -> None:
    """Stage B. The contract's own example, and the answer has to parse as what was asked for."""
    app = _app()
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "recipeName": {"type": "STRING"},
                "ingredients": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "propertyOrdering": ["recipeName", "ingredients"],
        },
    }
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(f"{BASE}/chat", json=_chat(responseSchema=schema))

    assert response.status_code == 200, response.text
    document = json.loads(response.json()["parts"][0]["text"])
    assert isinstance(document, list)
    assert set(document[0]) == {"recipeName", "ingredients"}
    assert isinstance(document[0]["ingredients"], list)


async def test_a_model_without_structured_output_refuses_rather_than_answering_in_prose() -> None:
    """The failure this capability check exists for. Prose returned to a caller that will call
    `JSON.parse` on it surfaces as a bug in *their* code, days later, with nothing pointing here."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"])
        response = client.post(f"{BASE}/chat", json=_chat(responseSchema={"type": "OBJECT"}))

    assert response.status_code == 400
    assert "structured output" in response.json()["message"]


async def test_an_unknown_schema_field_is_named_rather_than_dropped() -> None:
    """A schema **is** refused field by field, and unlike the request body it stays that way.

    The two are not the same question. A request field this surface does not model changes no
    answer, so it is accepted and named in a header. A *schema* field constrains the answer, and
    a dropped constraint produces output that is wrong in a way nothing about the response shows —
    `FRD-112` §2's reason, still the reason.

    The example used to be `additionalProperties`, which was never an unknown field so much as a
    missing one: it means the same thing in OpenAPI and in JSON Schema, every strict
    structured-output client emits it, and it is now part of the vocabulary. `unevaluatedItems` is
    draft 2020-12 with no OpenAPI 3.0 equivalent, which is the case this guards.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            f"{BASE}/chat",
            json=_chat(responseSchema={"type": "OBJECT", "unevaluatedItems": False}),
        )

    assert response.status_code == 400
    assert "unevaluatedItems" in response.json()["message"]


async def test_a_strict_schema_from_a_typed_client_is_forwarded_not_refused() -> None:
    """Measured against a real chatbot: it generates its schema from a typed model, so every
    object it describes carries `additionalProperties: false`, and every call came back `400`
    naming a field that means exactly what it says on both sides of the translation."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"])
        response = client.post(
            f"{BASE}/chat",
            json=_chat(
                responseSchema={
                    "type": "OBJECT",
                    "properties": {"answer": {"type": "STRING"}},
                    "additionalProperties": False,
                }
            ),
        )

    assert response.status_code == 200, response.text


async def test_a_declared_thinking_default_is_now_applied_rather_than_refused() -> None:
    """The case Stage A singled out and refused: the predecessor applies a model's declared default
    when the caller sends none. Stage B applies it too, which is what closes the difference."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={
                "modes": ["auto", "medium"],
                "default": {"mode": "medium"},
                "levels": {"medium": 1024},
            },
        )
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 200, response.text
    assert "thinking:medium budget=1024" in response.json()["parts"][0]["text"]


async def test_a_model_whose_default_is_disabled_is_served_normally() -> None:
    """Sending nothing *is* what that model asked for, so there is nothing to approximate."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["disabled", "auto"], "default": {"mode": "disabled"}},
        )
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 200, response.text


async def test_a_model_with_no_thinking_declaration_is_unaffected() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        assert client.post(f"{BASE}/chat", json=_chat()).status_code == 200


# == documents, which Stage A got early ==========================================================


async def test_stage_a_carries_documents_because_they_landed_first() -> None:
    """The plan had attachments arriving in Stage B. `FRD-110` landed before this surface did, and
    refusing a capability we have would be silly."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "attachments"],
            attachments={"media_types": {"application/pdf": {"tokens": 2000}}},
        )
        response = client.post(
            f"{BASE}/chat",
            json=_chat(
                request={
                    "parts": [
                        {"text": "was steht drin?"},
                        {
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(PDF).decode("ascii"),
                        },
                    ]
                }
            ),
        )

    assert response.status_code == 200, response.text
    assert "attachment" in response.json()["parts"][0]["text"]


async def test_a_model_that_cannot_read_the_document_refuses_on_this_surface_too() -> None:
    """The same rule, in the predecessor's vocabulary. A surface that quietly dropped the
    attachment would reintroduce exactly the failure `FRD-110` closed."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"])
        response = client.post(
            f"{BASE}/chat",
            json=_chat(
                request={
                    "parts": [
                        {"text": "was steht drin?"},
                        {
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(PDF).decode("ascii"),
                        },
                    ]
                }
            ),
        )

    assert response.status_code == 400
    assert "application/pdf" in response.json()["message"]


# == streaming ====================================================================================


async def test_streaming_chat_terminates_in_a_completed_event() -> None:
    """§2.2: typed events with a ``status`` field, ending in ``completed`` carrying the full
    response and its usage."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        with client.stream("POST", f"{BASE}/streaming-chat", json=_chat()) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            payload = "".join(response.iter_text())

    events = [json.loads(line[len("data: ") :]) for line in payload.split("\n\n") if line.strip()]
    assert events[-1]["status"] == "completed"
    assert "parts" in events[-1]["data"]
    assert events[-1]["data"]["usage_data"]["token_output"] > 0


# == embedding ====================================================================================


async def test_embed_returns_a_vector_in_the_predecessors_shape() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/embed", json={"text": "hallo", "model_id": 1004})

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"vector"}
    assert len(response.json()["vector"]) > 0


@pytest.mark.parametrize("text", ["", "   ", []])
async def test_empty_embedding_input_is_refused(text: Any) -> None:
    """The predecessor's rule (§2.3), and it prevents a class of accidental no-op billing."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/embed", json={"text": text, "model_id": 1004})

    assert response.status_code == 422


async def test_a_list_answers_one_vector_for_the_joined_text() -> None:
    """**Rewritten on 2026-08-12, because what it asserted turned out to be wrong.**

    It used to pin `vectors` — one per text — which `FRD-113` §11 had recorded as an *assumption*
    with the other reading written beside it, and asked to be confirmed against the running
    predecessor. The contract says the other one: the texts go as several parts of one
    call and it answers the documented singular `vector`.

    So the old assertion was faithful to the code and unfaithful to the contract — a test can only
    ever prove that those two agree. What makes the difference serious is that it was **data**, not
    an error: a caller indexing five chunks received five vectors where the predecessor gives one.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, embedding={"supports_batch": True})
        joined = client.post(f"{BASE}/embed", json={"text": ["ab", "cd"], "model_id": 1004})
        whole = client.post(f"{BASE}/embed", json={"text": "abcd", "model_id": 1004})

    assert joined.status_code == 200, joined.text
    body = joined.json()
    # The predecessor's singular key, and no plural one to fall back on.
    assert "vectors" not in body
    # And the vector is the one for the concatenation, which is what the provider does with several
    # parts of one content — measured, not assumed (`mapping.to_embedding`).
    assert body["vector"] == whole.json()["vector"]


async def test_a_model_without_batch_support_keeps_the_predecessors_code() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, embedding={"supports_batch": False})
        response = client.post(f"{BASE}/embed", json={"text": ["a", "b"], "model_id": 1004})

    assert response.status_code == 422
    assert response.json()["code"] == "EMBEDDING_AGGREGATION_NOT_SUPPORTED"


async def test_a_declared_task_type_is_honoured_and_an_undeclared_one_is_refused() -> None:
    """The whole value of the field is that the wrong one fails loudly instead of producing
    quietly worse vectors, so both halves are asserted together."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, embedding={"task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"]})
        good = client.post(
            f"{BASE}/embed",
            json={"text": "a", "model_id": 1004, "task_type": "RETRIEVAL_DOCUMENT"},
        )
        bad = client.post(
            f"{BASE}/embed", json={"text": "a", "model_id": 1004, "task_type": "CLUSTERING"}
        )
        default = client.post(f"{BASE}/embed", json={"text": "a", "model_id": 1004})

    assert good.status_code == 200, good.text
    assert bad.status_code == 422
    assert bad.json()["code"] == "INVALID_EMBEDDING_TASK_TYPE"
    # The predecessor's default is applied here, in the compatibility layer — so the same text
    # optimised two different ways gives two different vectors, which is what makes it real.
    assert default.status_code == 200
    assert default.json()["vector"] != good.json()["vector"]


async def test_a_model_without_embedding_capability_is_refused_with_its_own_code() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"])
        response = client.post(f"{BASE}/embed", json={"text": "a", "model_id": 1004})

    assert response.status_code == 422
    assert response.json()["code"] == "NO_EMBEDDING_CAPABILITIES"


# == the other endpoints ==========================================================================


async def test_models_lists_only_addressable_models() -> None:
    """A model with no numeric id cannot be called from this surface, so listing it would offer
    something that does not work. The catalog is where that is fixed."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, numeric_id=1004, capabilities=["generate", "embed"])
        listed = client.get(f"{BASE}/models").json()

    assert [m["id"] for m in listed] == [1004]
    assert set(listed[0]["capabilities"]) == {"CHAT", "EMBEDDING"}
    assert listed[0]["name"] == "mock-1"


async def test_health_and_version_info_answer_without_authentication() -> None:
    app = _app()
    with TestClient(app) as client:
        health = client.get(f"{BASE}/health")
        version = client.get(f"{BASE}/version-info")

    assert health.status_code == 200
    assert health.json()["status"] == "Healthy"
    # Absent build metadata is a valid state — a development run has no build number.
    assert version.status_code == 200
    assert "git" in version.json()


async def test_every_response_announces_that_the_surface_is_transitional() -> None:
    """`ADR-0010` Option C. A compatibility layer with no stated ending is a permanent one."""
    app = _app(kira_sunset="Wed, 31 Dec 2031 23:59:59 GMT")
    with TestClient(app) as client:
        await _catalogue(app)
        served = client.post(f"{BASE}/chat", json=_chat())
        refused = client.post(f"{BASE}/chat", json=_chat(model_id=9999))

    for response in (served, refused):
        assert response.headers["Deprecation"] == "true"
        assert "2031" in response.headers["Sunset"], "a refusal must announce it too"


# == the controls are shared, not copied ==========================================================


async def test_a_kira_request_is_audited_exactly_like_a_gemini_one() -> None:
    """The reason `api/serving` exists. A second surface with its own copy of the controls is the
    `:embedContent` failure with an extra hundred lines to hide in — so this compares the audit
    rows the two produce, which is the only way to be sure no step was skipped."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        client.post(f"{BASE}/chat", json=_chat())
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )

        async with app.state.db_sessionmaker() as session:
            rows = list(
                (await session.execute(select(RequestLog).order_by(RequestLog.api))).scalars()
            )

    assert {row.api for row in rows} == {"kira", "gemini"}
    for row in rows:
        assert row.outcome == "served"
        assert row.model == "mock-1"
        assert row.requested_model == "mock-1"
        assert row.total_tokens and row.total_tokens > 0
        assert row.latency_ms is not None
        assert row.degraded == {}


async def test_a_refusal_on_this_surface_is_recorded_too() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        client.post(f"{BASE}/chat", json=_chat(thinking={"mode": "high"}))

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert len(rows) == 1
    assert rows[0].api == "kira"
    assert rows[0].outcome == "invalid_request"
    assert rows[0].status == 422


async def test_models_reports_what_a_client_needs_to_ask_for_the_features() -> None:
    """A client reads this list to decide what to *request* — so a surface
    that serves thinking and task types while reporting neither leaves every caller concluding the
    models support none of it. The capability would exist and be unreachable, which from the
    outside is indistinguishable from not having built it."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "embed", "thinking"],
            max_output_tokens=65_536,
            thinking={
                "modes": ["auto", "limited", "disabled"],
                "min_tokens": 128,
                "max_tokens": 24_576,
                "default": {"mode": "disabled"},
            },
            embedding={
                "supports_batch": True,
                "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"],
                "dimensions": [768, 3072],
                "default": 768,
            },
        )
        listed = client.get(f"{BASE}/models").json()[0]

    assert listed["thinkingConfig"]["mode"] == ["auto", "disabled", "limited"]
    assert listed["thinkingConfig"]["minTokens"] == 128
    assert listed["thinkingConfig"]["maxTokens"] == 24_576
    assert listed["thinkingConfig"]["defaultThinking"]["mode"] == "disabled"
    assert listed["embedding_dimensions"] == 768
    assert listed["task_types"] == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
    assert listed["supports_aggregation"] is True


async def test_a_model_that_declares_no_thinking_reports_none_rather_than_an_empty_config() -> None:
    """ "Nobody has said" and "thinking exists here and nothing is allowed" are different answers,
    and an empty config would be the second."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate"])
        listed = client.get(f"{BASE}/models").json()[0]

    assert "thinkingConfig" not in listed


async def test_a_truncated_document_is_refused_on_the_streaming_path_too() -> None:
    """This surface's "stream" delivers one terminal event carrying the whole answer, so an
    incomplete document would arrive looking exactly like complete data. The check belongs on both
    paths or on neither."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "structured_output"], max_output_tokens=64)
        with client.stream(
            "POST",
            f"{BASE}/streaming-chat",
            json=_chat(
                responseSchema={"type": "OBJECT", "properties": {"a": {"type": "STRING"}}},
                maxTokens=1,
            ),
        ) as response:
            body = response.read().decode()

    # The failure cannot change the status — headers are already sent — so what is asserted is
    # that no `completed` event carrying the truncated document was emitted.
    assert "completed" not in body


# ---- an id that identifies two models (2026-08-08) -------------------------------------------


async def test_an_ambiguous_model_id_is_refused_rather_than_resolved_by_luck() -> None:
    """Two catalog entries claiming one integer id.

    Found live: a seed run for a second local model reused `9001`, and the resolver's
    `scalar_one_or_none()` raised — a **500** on the predecessor's surface, for a caller who did
    nothing wrong. Picking one of the two would have been worse: the answer would be served, billed
    and audited under a model the caller never named, and nothing in the response would look wrong.
    That is `ADR-0011`'s ambiguous routing table, in the catalog.

    503, because the installation is misconfigured and an administrator can fix it — and the two
    model names stay in the log, since which models an installation runs is not this surface's to
    disclose.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, model="one-1", numeric_id=9001)
        await _catalogue(app, model="two-2", numeric_id=9001)

        response = client.post(f"{BASE}/chat", json=_chat(model_id=9001))

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "MODEL_NOT_FOUND"
    assert "uniquely" in body["message"]
    assert "one-1" not in response.text and "two-2" not in response.text


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (["ok", ""], "an empty element disappears into the join"),
        (["ok", "   "], "whitespace contributes nothing either"),
        ([""], "a single blank is the same thing with one element"),
        ([], "an empty list asks for an embedding of nothing"),
        ("", "and so does a bare empty string"),
    ],
)
async def test_a_blank_embedding_text_is_refused(text: object, why: str) -> None:
    """**The join is what makes this worth refusing.** A list is one embedding
    (`FRD-113` §11), so `["ok", ""]` embedded exactly like `["ok"]` — 200, a perfectly normal
    vector, and no way for the caller to learn that one of their chunks was empty. A silent drop
    in the one place nobody can see it.

    The canonical validator has always refused a blank text; it never saw one here, because this
    surface joins *before* validating. Refused at the wire now, which is also where the message
    can name the position.
    """
    app = _app()
    with TestClient(app) as client:
        # **Batch support is declared on purpose.** Without it every list is refused as
        # `EMBEDDING_AGGREGATION_NOT_SUPPORTED`, and this test passed against a gateway with the
        # blank rule deleted — a 422 for a reason that has nothing to do with what it is named
        # after. Found by breaking the rule and watching nothing go red.
        await _catalogue(app, capabilities=["embed"], embedding={"supports_batch": True})
        response = client.post(f"{BASE}/embed", json={"text": text, "model_id": 1004})

    assert response.status_code == 422, f"{why}: {response.text[:200]}"

    # **The contract's own code**, not the generic one. This was first written as a schema
    # validator, which makes any refusal a `VALIDATION_ERROR` — and the contract has a code for
    # exactly this case, which a migrating client's error handling switches on. Caught by the
    # integration layer against the running stack, because the hermetic version of this assertion
    # only asked whether *some* code was present.
    assert response.json()["code"] == "EMPTY_EMBEDDING_INPUT", response.text[:200]


async def test_a_list_of_real_texts_is_still_one_embedding() -> None:
    """The rule above must not have narrowed the feature it guards: a list still works."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"], embedding={"supports_batch": True})
        response = client.post(
            f"{BASE}/embed", json={"text": ["hello", " world"], "model_id": 1004}
        )

    assert response.status_code == 200, response.text[:300]
    assert isinstance(response.json()["vector"], list)


async def test_an_embedding_model_reports_its_width_and_batch_support() -> None:
    """What a client sizing a vector store reads.

    The fields have been in this response since Stage B and the *seed* declared none of them, so
    `all-minilm` listed with a batch flag and no width — which a reader takes as "this model has
    no fixed width", not as "nobody wrote it down". `FRD-114` FR-7 forbids inventing it, so it was
    measured (384, stable across two texts of very different length) and declared.
    """
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["embed"],
            embedding={"supports_batch": True, "dimensions": [384], "default": 384},
        )
        response = client.get(f"{BASE}/models")

    entry = next(m for m in response.json() if m["id"] == 1004)
    assert entry["embedding_dimensions"] == 384
    assert entry["supports_aggregation"] is True


async def test_health_says_that_an_upstream_verdict_is_cached() -> None:
    """The endpoint reports a **cached** probe, and the shape had no way to admit it.

    Deliberate: probing every upstream per call makes this as slow as the slowest provider, bills
    somebody for asking whether a model is alive, and can wake a scaled-to-zero endpoint
    (`FRD-117` §5.2). But `time_taken` carries the *last probe's* duration and reads exactly like a
    measurement taken just now — asked directly whether this was cached, the honest answer was yes
    and nothing in the response said so. A figure that invites the wrong reading is the same defect
    as a wrong figure.

    A tag, because a compatibility surface does not get to add fields to somebody else's shape —
    but it does get to say something true in the space it has.
    """
    app = _app()
    with TestClient(app) as client:
        response = client.get(f"{BASE}/health")

    entities = response.json()["entities"]
    upstreams = [e for e in entities if "upstream" in e["tags"]]
    assert upstreams, "no upstream reported, so this asserts nothing"
    for entity in upstreams:
        assert any(t.startswith("cached:") or t == "not-probed" for t in entity["tags"]), entity
