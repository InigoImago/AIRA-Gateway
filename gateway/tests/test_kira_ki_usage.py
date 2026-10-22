"""`/ki-usage` answers about the caller, per model, as JSON or CSV (`FRD-146` FR-21, FR-22).

The predecessor keys its rows by user and model. Here the user is always the one asking: another
person's figures are the console's reports to show, under their own rules, and a second door to
them would need a second copy of those rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog

URL = "/kira/api/external/ki-usage?startDatum=2026-10-01&endDatum=2026-11-01"
WHEN = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

ADA = Principal(subject="ada", method="oidc", username="ada", use_cases=("uc",))
#: Every figure in the console, and still only their own here.
ADMIN = Principal(subject="root", method="oidc", username="root", roles=("global-admin",))
#: A credential that names no person: answered with nothing, never with everybody.
NOBODY = Principal(subject="", method="oidc", username="")


def _row(person: str, model: str, prompt: int, completion: int) -> RequestLog:
    return RequestLog(
        subject=person,
        username=person,
        auth_method="oidc",
        use_case="uc",
        api="kira",
        operation="chat",
        model=model,
        status=200,
        outcome="served",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        created_at=WHEN,
    )


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


async def _seed(client: TestClient) -> None:
    async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
        session.add(ModelRead(model="chat-model", numeric_id=1004, capabilities=["generate"]))
        session.add(ModelRead(model="embed-model", numeric_id=2003, capabilities=["embed"]))
        session.add(_row("ada", "chat-model", 10, 20))
        session.add(_row("ada", "chat-model", 1, 2))
        session.add(_row("ada", "embed-model", 5, 0))
        session.add(_row("bob", "chat-model", 1000, 2000))
        session.add(_row("root", "chat-model", 7, 7))
        await session.commit()


def _by_model(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {row["model_id"]: row for row in rows}


async def test_a_caller_gets_their_own_rows_one_per_model() -> None:
    with _client(ADA) as client:
        await _seed(client)
        response = client.get(URL)

    assert response.status_code == 200, response.text
    rows = _by_model(response.json())
    assert rows == {
        1004: {
            "user_id": "ada",
            "model_id": 1004,
            "entry_count": 2,
            "token_input_sum": 11,
            "token_output_sum": 22,
        },
        2003: {
            "user_id": "ada",
            "model_id": 2003,
            "entry_count": 1,
            "token_input_sum": 5,
            "token_output_sum": 0,
        },
    }


async def test_no_role_turns_it_into_a_list_of_colleagues() -> None:
    """A Global Administrator reads everybody's figures in the console. Here they read their own."""
    with _client(ADMIN) as client:
        await _seed(client)
        rows = client.get(URL).json()

    assert {row["user_id"] for row in rows} == {"root"}
    assert rows[0]["token_input_sum"] == 7


async def test_a_credential_without_a_person_is_answered_with_nothing() -> None:
    """`person=None` is the whole installation to the report. An empty name must not become it."""
    with _client(NOBODY) as client:
        await _seed(client)
        response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == []


async def test_csv_carries_the_same_rows_after_a_byte_order_mark() -> None:
    with _client(ADA) as client:
        await _seed(client)
        response = client.get(URL, headers={"Accept": "text/csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert text.startswith("﻿"), "a spreadsheet reads it as Latin-1 without the mark"
    lines = text.removeprefix("﻿").splitlines()
    assert lines[0] == "user_id,model_id,entry_count,token_input_sum,token_output_sum"
    assert sorted(lines[1:]) == ["ada,1004,2,11,22", "ada,2003,1,5,0"]
    assert "bob" not in text


@pytest.mark.parametrize(
    ("query", "status", "code"),
    [
        ("startDatum=2026-10-01", 400, "MISSING_QUERY_PARAM"),
        ("startDatum=yesterday&endDatum=2026-11-01", 422, "VALIDATION_ERROR"),
        ("startDatum=2026-11-01&endDatum=2026-10-01", 400, "INVALID_TIME_RANGE"),
    ],
)
def test_a_window_it_cannot_read_is_refused_in_the_predecessors_words(
    query: str, status: int, code: str
) -> None:
    with _client(ADA) as client:
        response = client.get(f"/kira/api/external/ki-usage?{query}")

    assert response.status_code == status
    assert response.json()["code"] == code
