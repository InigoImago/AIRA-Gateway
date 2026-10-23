"""What a body costs is its structure, and the structure is bounded before anything parses it.

An 8 MiB body of 645 000 minimal parts (`{"text":"a"}`) was accepted by every bound this gateway
had — the byte ceiling, the depth — and cost 2.9 s of the event loop every use case shares and
830 MB before the rate limit, a suspension or a budget could refuse it; the KIRA surface's
150 000-turn history cost 3.1 s. Two bounds now apply, each before the cost it bounds:

1. **values** (`aira_common.nesting.MAX_JSON_VALUES`), counted on the raw bytes before
   `json.loads`, which is where the cost starts — a named `400`, on KIRA the `422 VALIDATION_ERROR`
   of a body that is not a request;
2. **turns, parts and functions** (`core.canonical`), counted on the parsed body before a surface
   validates any of them, because validating a part is the most expensive thing a body asks for.

Both are refusals of an attributed request, so both are **recorded**, like every other.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from aira_common.nesting import MAX_JSON_VALUES
from aira_gateway.api.serving import body as body_reader
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import MAX_MESSAGE_PARTS, MAX_MESSAGES, MAX_TOOLS
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead

ADMIN = Principal(
    subject="root",
    method="api_key",
    roles=("global-admin", "it-security"),
    use_cases=("uc",),
    username="root",
    credential="key-1",
)

GENERATE = "/v1beta/models/mock-1:generateContent"
KIRA_CHAT = "/kira/api/external/chat"

#: Every route that reads a hand-written body, with the status its surface refuses a body with.
ROUTES: list[tuple[str, int]] = [
    (GENERATE, 400),
    ("/v1beta/models/mock-1:streamGenerateContent", 400),
    ("/v1beta/models/mock-1:embedContent", 400),
    ("/v1beta/models/mock-1:batchEmbedContents", 400),
    ("/v1/projects/p/locations/global/rankingConfigs/c:rank", 400),
    ("/v1beta/pipeline:dryRun", 400),
    ("/v1beta/suspensions", 400),
    ("/v1beta/models/mock-1:checkThinking", 400),
    (KIRA_CHAT, 422),
    ("/kira/api/external/streaming-chat", 422),
    ("/kira/api/external/embed", 422),
    ("/kira/api/external/rerank", 422),
]

#: The routes whose refusals are recorded: the model verbs of both surfaces.
RECORDED = [GENERATE, KIRA_CHAT, "/kira/api/external/embed", "/kira/api/external/rerank"]


def _app() -> Any:
    # `log_queue_size=0` writes the audit row on the request path, so a test can count it.
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _seed(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
        session.add(
            ModelRead(model="mock-1", numeric_id=1004, capabilities=["generate", "tools", "embed"])
        )
        await session.commit()


async def _rows(app: Any) -> int:
    async with app.state.db_sessionmaker() as session:
        return int((await session.execute(select(func.count(RequestLog.id)))).scalar_one())


def _client(app: Any) -> TestClient:
    app.dependency_overrides[require_principal] = lambda: ADMIN
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, path: str, raw: bytes) -> Any:
    return client.post(
        path, content=raw, headers={"content-type": "application/json", "x-aira-use-case": "uc"}
    )


def _values(count: int) -> bytes:
    """An object holding ``count`` values in all: itself, one list, and numbers. One level deep, so
    the values are the only bound it can meet."""
    return b'{"x":[' + b",".join([b"0"] * (count - 2)) + b"]}"


def _parts(count: int) -> bytes:
    """The finding's own shape: one turn of ``count`` minimal parts."""
    parts = b",".join([b'{"text":"a"}'] * count)
    return b'{"contents":[{"role":"user","parts":[' + parts + b"]}]}"


# == the values, before the parse ================================================================


@pytest.mark.parametrize(("path", "status"), ROUTES)
async def test_a_body_holding_more_values_than_the_bound_is_a_named_refusal(
    path: str, status: int
) -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = _post(client, path, _values(MAX_JSON_VALUES + 1))

    assert response.status_code == status, (path, response.text[:300])
    assert f"more than {MAX_JSON_VALUES} JSON values" in response.text, (path, response.text[:300])
    if status == 422:
        assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("path", [route for route, _status in ROUTES])
async def test_a_body_at_the_value_bound_is_not_refused_for_its_values(path: str) -> None:
    """The other half: every route refuses this body for something it *says* — a missing field —
    and none for how much of it there is. A bound that fires one value early is one nobody can
    work out from its message."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = _post(client, path, _values(MAX_JSON_VALUES))

    assert "JSON values" not in response.text, (path, response.text[:300])
    assert response.status_code < 500, (path, response.text[:300])


@pytest.mark.parametrize("path", RECORDED)
async def test_a_body_refused_for_its_values_is_recorded(path: str) -> None:
    """The log records what was asked (`FRD-122`): an attributed caller refused at the door is a
    row, which is also what lets an anomaly rule see somebody sending them."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        before = await _rows(app)
        response = _post(client, path, _values(MAX_JSON_VALUES + 1))
        after = await _rows(app)

    assert 400 <= response.status_code < 500, response.text[:300]
    assert after == before + 1, path


@pytest.mark.parametrize("path", [GENERATE, KIRA_CHAT])
async def test_the_finding_s_body_is_refused_unread(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Unread** is the property, not merely refused: the parse is the cost. The decoder is
    replaced by one that fails the test if it is asked — refused afterwards, by a rule further on,
    would be the defect with a different message."""

    class _Unread:
        @staticmethod
        def loads(text: str) -> Any:
            raise AssertionError("the body was parsed before its structure was measured")

        dumps = staticmethod(json.dumps)

    app = _app()
    with _client(app) as client:
        await _seed(app)
        monkeypatch.setattr(body_reader, "json", _Unread)
        response = _post(client, path, _parts(MAX_JSON_VALUES // 2))

    assert 400 <= response.status_code < 500, response.text[:300]
    assert "JSON values" in response.text


# == turns, parts and functions, before they are validated ========================================


def _turns(count: int, part: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [{"role": "user", "parts": [part or {"text": "a"}]} for _ in range(count)]


def _generate(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post(GENERATE, json=body, headers={"x-aira-use-case": "uc"})


async def test_more_turns_than_a_request_may_carry_is_refused_by_name() -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = _generate(client, {"contents": _turns(MAX_MESSAGES + 1)})

    assert response.status_code == 400, response.text[:300]
    assert f"more than the {MAX_MESSAGES} one request may" in response.text


async def test_the_turns_are_counted_before_any_of_them_is_validated() -> None:
    """Every turn here is invalid, and the refusal is about how many there are: had the parse
    validated them first, it would have named the first bad part — after paying for all of them."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = _generate(client, {"contents": _turns(MAX_MESSAGES + 1, {"bogus": 1})})

    assert response.status_code == 400
    assert f"more than the {MAX_MESSAGES} one request may" in response.text, response.text[:300]


async def test_more_parts_than_a_request_may_carry_is_refused_by_name() -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        turn = {"role": "user", "parts": [{"bogus": 1}] * (MAX_MESSAGE_PARTS + 1)}
        response = _generate(client, {"contents": [turn]})

    assert response.status_code == 400
    assert f"more than the {MAX_MESSAGE_PARTS} one request may" in response.text


async def test_a_conversation_at_the_bounds_is_served() -> None:
    """The comparison: as many turns and parts as the bounds allow is an ordinary request."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        turns = _turns(MAX_MESSAGES - 1)
        turns.append({"role": "user", "parts": [{"text": "a"}] * (MAX_MESSAGE_PARTS - len(turns))})
        response = _generate(client, {"contents": turns})

    assert response.status_code == 200, response.text[:300]


def _declarations(count: int) -> dict[str, Any]:
    return {
        "contents": _turns(1),
        "tools": [{"functionDeclarations": [{"name": f"f{index}"} for index in range(count)]}],
    }


async def test_more_functions_than_a_request_may_offer_is_refused_on_the_gemini_surface() -> None:
    """Google's own maximum, which the KIRA surface already applied: past it the Gemini surface
    parsed and mapped as many declarations as the body held — 78 000 in one request."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        refused = _generate(client, _declarations(MAX_TOOLS + 1))
        served = _generate(client, _declarations(MAX_TOOLS))

    assert refused.status_code == 400, refused.text[:300]
    assert f"more than the {MAX_TOOLS} one request may offer" in refused.text
    assert served.status_code == 200, served.text[:300]


async def test_functions_are_counted_across_every_tool_and_in_a_single_tool_object() -> None:
    """Google takes a list of tools or one tool object, and the count is the request's, however
    the declarations are split across them."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        split = {
            "contents": _turns(1),
            "tools": [
                {"functionDeclarations": [{"name": f"a{index}"} for index in range(MAX_TOOLS)]},
                {"functionDeclarations": [{"name": "one_too_many"}]},
            ],
        }
        single = {
            "contents": _turns(1),
            "tools": {"functionDeclarations": [{"name": f"f{i}"} for i in range(MAX_TOOLS + 1)]},
        }
        responses = [_generate(client, split), _generate(client, single)]

    for response in responses:
        assert response.status_code == 400, response.text[:300]
        assert "functions, more than the" in response.text


# == the same bounds on the KIRA surface ==========================================================


def _kira(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post(KIRA_CHAT, json=body, headers={"x-aira-use-case": "uc"})


def _history(count: int) -> list[dict[str, Any]]:
    return [{"role": "user", "content": {"parts": [{"text": "a"}]}} for _ in range(count)]


async def test_a_kira_history_longer_than_a_request_may_carry_is_its_validation_error() -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        body = {
            "model_id": 1004,
            "request": {"parts": [{"text": "hi"}]},
            "conversation_history": _history(MAX_MESSAGES),
        }
        response = _kira(client, body)

    assert response.status_code == 422, response.text[:300]
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert f"more than the {MAX_MESSAGES} one request may" in response.text


async def test_kira_parts_past_the_bound_are_its_validation_error() -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        body = {"model_id": 1004, "request": {"parts": [{"text": "a"}] * (MAX_MESSAGE_PARTS + 1)}}
        response = _kira(client, body)

    assert response.status_code == 422, response.text[:300]
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert f"more than the {MAX_MESSAGE_PARTS} one request may" in response.text


async def test_a_kira_conversation_at_the_bounds_is_served() -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        body = {
            "model_id": 1004,
            "request": {"parts": [{"text": "a"}] * (MAX_MESSAGE_PARTS - MAX_MESSAGES + 1)},
            "conversation_history": _history(MAX_MESSAGES - 1),
        }
        response = _kira(client, body)

    assert response.status_code == 200, response.text[:300]
