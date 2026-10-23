"""`/ki-usage` answers about the caller, per model, as JSON or CSV (`FRD-146` FR-21, FR-22).

The predecessor keys its rows by user and model. Here the user is always the one asking: another
person's figures are the console's reports to show, under their own rules, and a second door to
them would need a second copy of those rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog

URL = "/kira/api/external/ki-usage?startDatum=2026-10-01&endDatum=2026-11-01"
WHEN = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

ADA = Principal(subject="ada", method="oidc", username="ada", use_cases=("uc",))
#: Every figure in the console, and still only their own here — in the use cases they act in.
ADMIN = Principal(
    subject="root", method="oidc", username="root", roles=("global-admin",), use_cases=("uc",)
)
#: A credential that names no person: answered with nothing, never with everybody — even where it
#: may act in a use case full of other people's traffic.
NOBODY = Principal(subject="", method="oidc", username="", use_cases=("uc",))


def _row(
    person: str, model: str, prompt: int, completion: int, *, use_case: str = "uc"
) -> RequestLog:
    return RequestLog(
        subject=person,
        username=person,
        auth_method="oidc",
        use_case=use_case,
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


async def test_seconds_since_1970_are_a_window_too() -> None:
    """The predecessor takes Unix time as well as ISO 8601; seconds are UTC by definition, so the
    window is the same one."""
    start = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    end = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())
    with _client(ADA) as client:
        await _seed(client)
        iso = client.get(URL).json()
        unix = client.get(f"/kira/api/external/ki-usage?startDatum={start}&endDatum={end}")

    assert unix.status_code == 200, unix.text
    assert _by_model(unix.json()) == _by_model(iso)


def test_seconds_no_clock_can_hold_are_a_validation_error() -> None:
    with _client(ADA) as client:
        response = client.get("/kira/api/external/ki-usage?startDatum=1&endDatum=" + "9" * 30)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


#: A key an administrator of `uc` issued in ada's name (`FRD-604` FR-5): it acts as ada, in `uc`.
ADAS_KEY_IN_UC = Principal(
    subject="ada", method="api_key", username="ada", credential="k-uc", use_cases=("uc",)
)


async def test_a_key_answers_for_its_own_use_case_and_no_other() -> None:
    """A key issued in a member's name answers for that member — in the key's use case only.

    Whoever holds it (the administrator who issued it has the plaintext) must not read what the
    member did in use cases they have no part in: the console would not show it to them, and
    `/v1beta/me/budget` does not either.
    """
    with _client(ADAS_KEY_IN_UC) as client:
        await _seed(client)
        async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(_row("ada", "chat-model", 4000, 6000, use_case="hr-confidential"))
            await session.commit()
        rows = _by_model(client.get(URL).json())

    assert rows[1004]["token_input_sum"] == 11, "hr-confidential leaked into a key bound to uc"
    assert rows[1004]["entry_count"] == 2


async def test_a_person_sees_their_own_figures_in_each_use_case_they_act_in() -> None:
    """A person signed in reads their traffic in every use case they belong to — and not in one
    they have left."""
    both = Principal(subject="ada", method="oidc", username="ada", use_cases=("uc", "lab"))
    with _client(both) as client:
        await _seed(client)
        async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(_row("ada", "chat-model", 100, 200, use_case="lab"))
            session.add(_row("ada", "chat-model", 9000, 9000, use_case="left-long-ago"))
            await session.commit()
        rows = _by_model(client.get(URL).json())

    assert rows[1004]["token_input_sum"] == 111
    assert rows[1004]["entry_count"] == 3


async def test_a_credential_that_may_act_nowhere_is_answered_with_nothing() -> None:
    """No use case is no scope, never every scope: an unbound key reads nothing here."""
    unbound = Principal(subject="ada", method="api_key", username="ada", credential="k")
    with _client(unbound) as client:
        await _seed(client)
        response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    ("start", "end", "status"),
    [
        ("2025-10-01", "2026-10-02", 200),  # 366 days: a leap year's worth
        ("2025-10-01", "2026-10-03", 400),
        ("0001-01-01", "9999-12-31", 400),
        ("1", str(int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())), 400),
    ],
)
def test_a_window_is_at_most_a_year(start: str, end: str, status: int) -> None:
    """The console's bound. Anybody holding a credential may call this, and a window over all of
    history makes one call aggregate every row a person ever caused."""
    with _client(ADA) as client:
        response = client.get(f"/kira/api/external/ki-usage?startDatum={start}&endDatum={end}")

    assert response.status_code == status, response.text
    if status == 400:
        assert response.json()["code"] == "INVALID_TIME_RANGE"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-01", "2026-11-01T00:00:00+00:00"),
        ("2026-10-01T02:00:00+02:00", "2026-11-01"),
        ("2026-10-01", str(int(datetime(2026, 11, 1, tzinfo=UTC).timestamp()))),
    ],
)
async def test_a_date_and_a_time_with_an_offset_make_one_window(start: str, end: str) -> None:
    """Seconds since 1970 carry a zone and a plain date does not: the two ends are compared in UTC
    rather than refused by the comparison itself, which raised a `500` and left no row."""
    with _client(ADA) as client:
        await _seed(client)
        response = client.get(
            "/kira/api/external/ki-usage", params={"startDatum": start, "endDatum": end}
        )

    assert response.status_code == 200, response.text
    assert _by_model(response.json())[1004]["token_input_sum"] == 11


@contextmanager
def _statements_over_the_request_log(client: TestClient) -> Iterator[list[str]]:
    engine = client.app.state.db_engine.sync_engine  # type: ignore[attr-defined]
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "request_logs" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_the_answer_is_one_query_over_the_request_log() -> None:
    """Every credential holder may ask this, so it asks the one aggregate it answers with — not the
    console's whole report, six more aggregates over the same rows that nobody reads."""
    with _client(ADA) as client:
        await _seed(client)
        with _statements_over_the_request_log(client) as seen:
            assert client.get(URL).status_code == 200

    assert len(seen) == 1, seen
    # `nullif` is the grouping expression's: compared as a whole, it reaches no index.
    assert "nullif" not in seen[0].lower(), "the person filter is the expression no index holds"
