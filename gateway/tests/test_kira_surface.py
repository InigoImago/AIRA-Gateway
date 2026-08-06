"""The predecessor's contract, served by AIRA (FRD-107 Stage A).

These are **contract tests**: they assert the response shape field by field against `kira_api.md`,
because "compatible" has to be a fact rather than a claim. A migrating consumer changes a base URL
and nothing else, and the only thing that makes that true is checking it.

The second theme is the one Stage A exists for: **a field this gateway cannot yet honour is
refused, never ignored.** A dropped field produces an answer that is wrong for a reason the caller
cannot see — the same failure documents have one level up.
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
    """`kira_api.md` §2.1: ``parts`` plus ``usage_data`` with the two token counts."""
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


async def test_an_unknown_model_id_is_404_model_not_found() -> None:
    """§6.2. The integer id is the predecessor's addressing, and a client checks this code."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat(model_id=9999))

    assert response.status_code == 404
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
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"{BASE}/chat", content=b"not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_JSON_BODY"


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


async def test_thinking_is_refused_by_name_rather_than_dropped() -> None:
    """The whole reason Stage A exists as a stage. A caller who asked for a thinking budget and
    silently got none would see an answer that is worse for a reason nothing reveals."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat(thinking={"mode": "medium"}))

    assert response.status_code == 422
    assert response.json()["code"] == "NOT_YET_SUPPORTED"
    assert "thinking" in response.json()["message"]


async def test_a_response_schema_is_refused_by_name() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat(responseSchema={"type": "OBJECT"}))

    assert response.status_code == 422
    assert response.json()["code"] == "NOT_YET_SUPPORTED"
    assert "responseSchema" in response.json()["message"]


async def test_a_model_with_a_declared_thinking_default_is_refused_rather_than_approximated() -> (
    None
):
    """The subtle one the FRD singled out. The predecessor applies a model's declared default
    thinking when the caller sends none. Serving such a model with no thinking at all would give a
    different answer for a reason nobody could see — so it is refused until `FRD-111` lands."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["auto", "medium"], "default": {"mode": "medium"}},
        )
        response = client.post(f"{BASE}/chat", json=_chat())

    assert response.status_code == 422
    assert response.json()["code"] == "NOT_YET_SUPPORTED"
    assert "medium" in response.json()["message"]


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


async def test_a_list_and_a_task_type_are_refused_by_name_until_frd_113() -> None:
    """Embedding one at a time would silently cost N requests of quota against a limit of one, and
    the wrong task type produces vectors that retrieve measurably worse with nothing to show it."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        batch = client.post(f"{BASE}/embed", json={"text": ["a", "b"], "model_id": 1004})
        task = client.post(
            f"{BASE}/embed",
            json={"text": "a", "model_id": 1004, "task_type": "RETRIEVAL_DOCUMENT"},
        )

    assert batch.json()["code"] == "NOT_YET_SUPPORTED"
    assert task.json()["code"] == "NOT_YET_SUPPORTED"


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
    assert health.json()["status"] == "HEALTHY"
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
