"""The per-use-case trace overview (FRD-502).

Driven through the **route**, not against the query, for the reason `FRD-124` and `FRD-602` both
paid for once: two correct halves and no wire between them is invisible to coverage. It is also the
only way to assert what this feature really promises — that no payload reaches the reader — since
that is a property of the response body rather than of a SELECT.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anyio
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import RequestLog

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _row(seconds_ago: int = 0, **over) -> RequestLog:
    values = {
        "id": str(uuid.uuid4()),
        "subject": "alice",
        "auth_method": "api_key",
        "use_case": "uc-a",
        "api": "gemini",
        "operation": "generateContent",
        "model": "mock-1",
        "status": 200,
        "outcome": "served",
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "latency_ms": 40,
        "cost_nanos": 1000,
        "created_at": NOW - timedelta(seconds=seconds_ago),
    }
    values.update(over)
    return RequestLog(**values)


async def _seed(sessions, *rows: RequestLog) -> None:
    async with sessions() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _client(principal: Principal | None = None) -> TestClient:
    """A client whose caller is whoever the test says, with a database it owns."""
    app = create_app(GatewaySettings(auth_required=False))
    if principal is not None:
        app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def _fill(client: TestClient, *rows: RequestLog) -> None:
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(_seed, client.app.state.db_sessionmaker, *rows)


# ---- what a trace is, and is not ---------------------------------------------------------------


def test_a_trace_row_never_carries_a_payload() -> None:
    """`FRD-502` FR-11, asserted on the **response body**.

    The field list is an allow-list rather than an exclusion list, so a column added to
    `request_logs` tomorrow cannot appear here because somebody forgot to exclude it — and the two
    that must never appear are exactly the ones a forgotten exclusion would leak.
    """
    with _client() as client:
        _fill(
            client,
            _row(
                request_payload={"contents": [{"parts": [{"text": "a secret prompt"}]}]},
                response_payload={"candidates": [{"content": "a secret answer"}]},
            ),
        )
        response = client.get("/v1beta/traces")

    assert response.status_code == 200
    body = json.dumps(response.json())
    assert response.json()["traces"], "the row was not returned at all"
    assert "secret prompt" not in body
    assert "secret answer" not in body
    assert "request_payload" not in body
    assert "response_payload" not in body


def test_a_trace_row_carries_what_an_investigation_needs() -> None:
    with _client() as client:
        _fill(client, _row(requested_model="gemini-2.0-flash", model_selection="fallback:1"))
        row = client.get("/v1beta/traces").json()["traces"][0]

    for field in (
        "created_at",
        "operation",
        "model",
        "requested_model",
        "model_selection",
        "status",
        "outcome",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "cost_nanos",
        "trace_id",
        "subject",
        "credential",
        "use_case",
    ):
        assert field in row, field
    # Requested and served are both there: with cross-vendor chains they differ, and "why did the
    # Anthropic spend triple" has no answer without the pair.
    assert row["requested_model"] == "gemini-2.0-flash"
    assert row["model_selection"] == "fallback:1"


def test_the_newest_request_is_first() -> None:
    with _client() as client:
        _fill(client, _row(seconds_ago=60, model="old"), _row(seconds_ago=1, model="new"))
        rows = client.get("/v1beta/traces").json()["traces"]

    assert [r["model"] for r in rows] == ["new", "old"]


# ---- scope --------------------------------------------------------------------------------------


def test_a_member_sees_their_own_use_case_and_nobody_elses() -> None:
    caller = Principal(subject="alice", method="oidc", use_cases=("uc-a",))
    with _client(caller) as client:
        _fill(client, _row(use_case="uc-a"), _row(use_case="uc-b"))
        body = client.get("/v1beta/traces").json()

    assert {r["use_case"] for r in body["traces"]} == {"uc-a"}
    assert body["scope"] == "use_cases"


def test_an_oversight_role_sees_every_use_case() -> None:
    caller = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
    with _client(caller) as client:
        _fill(client, _row(use_case="uc-a"), _row(use_case="uc-b"))
        body = client.get("/v1beta/traces").json()

    assert {r["use_case"] for r in body["traces"]} == {"uc-a", "uc-b"}
    assert body["scope"] == "all"


def test_a_caller_with_no_use_cases_gets_an_empty_list_rather_than_a_refusal() -> None:
    """ "There is nothing here" and "you may not look" are different answers (`FRD-601`)."""
    caller = Principal(subject="nobody", method="oidc")
    with _client(caller) as client:
        _fill(client, _row())
        response = client.get("/v1beta/traces")

    assert response.status_code == 200
    assert response.json()["traces"] == []


def test_asking_about_somebody_elses_use_case_is_emptiness_not_a_refusal() -> None:
    """A 403 would confirm the use case exists to somebody who may not see it."""
    caller = Principal(subject="alice", method="oidc", use_cases=("uc-a",))
    with _client(caller) as client:
        _fill(client, _row(use_case="uc-b"))
        response = client.get("/v1beta/traces?use_case=uc-b")

    assert response.status_code == 200
    assert response.json()["traces"] == []


def test_the_two_kinds_of_empty_are_told_apart() -> None:
    """ "Nothing happened" and "you can see nothing here" are both an empty list, and a screen that
    prints the first when it means the second sends its reader looking for a bug in the recording.

    `in_scope` says which, and says it about the **caller's own visibility** — it confirms nothing
    about whether the use case exists, so the reason a 403 was refused still holds.
    """
    caller = Principal(subject="alice", method="oidc", use_cases=("uc-a",))
    with _client(caller) as client:
        _fill(client, _row(use_case="uc-a"))

        nothing_yet = client.get("/v1beta/traces?use_case=uc-a&outcome=client_gone").json()
        assert nothing_yet["traces"] == []
        assert nothing_yet["in_scope"] is True

        not_visible = client.get("/v1beta/traces?use_case=uc-b").json()
        assert not_visible["traces"] == []
        assert not_visible["in_scope"] is False

    blind = Principal(subject="nobody", method="oidc")
    with _client(blind) as client:
        _fill(client, _row())
        assert client.get("/v1beta/traces").json()["in_scope"] is False


def test_the_endpoint_needs_a_credential() -> None:
    """A trace names a caller, a credential and a model. It is not public."""
    with TestClient(create_app(GatewaySettings(auth_required=True))) as client:
        assert client.get("/v1beta/traces").status_code == 401


# ---- filters ------------------------------------------------------------------------------------


def test_it_filters_to_one_use_case() -> None:
    with _client() as client:
        _fill(client, _row(use_case="uc-a"), _row(use_case="uc-b"))
        rows = client.get("/v1beta/traces?use_case=uc-a").json()["traces"]

    assert {r["use_case"] for r in rows} == {"uc-a"}


def test_it_filters_to_one_outcome() -> None:
    with _client() as client:
        _fill(client, _row(outcome="served"), _row(outcome="rate_limited", status=429))
        rows = client.get("/v1beta/traces?outcome=rate_limited").json()["traces"]

    assert [r["outcome"] for r in rows] == ["rate_limited"]


def test_refusals_only_is_everything_that_was_not_served() -> None:
    """The shape somebody investigating actually asks for — and it must not need them to know the
    closed vocabulary by heart."""
    with _client() as client:
        _fill(
            client,
            _row(outcome="served"),
            _row(outcome="rate_limited", status=429),
            _row(outcome="suspended", status=429),
            _row(outcome="client_gone", status=499),
        )
        rows = client.get("/v1beta/traces?refusals_only=true").json()["traces"]

    assert {r["outcome"] for r in rows} == {"rate_limited", "suspended", "client_gone"}


# ---- paging -------------------------------------------------------------------------------------


def test_a_page_carries_a_cursor_only_when_there_is_more() -> None:
    with _client() as client:
        _fill(client, *[_row(seconds_ago=i) for i in range(3)])

        full = client.get("/v1beta/traces?limit=2").json()
        assert len(full["traces"]) == 2
        assert full["next_cursor"]

        last = client.get(f"/v1beta/traces?limit=2&cursor={full['next_cursor']}").json()
        assert len(last["traces"]) == 1
        assert last["next_cursor"] is None


def test_paging_shows_no_row_twice_and_skips_none_when_the_table_grows() -> None:
    """Rows arrive while somebody reads. Offset paging under an appending table shows duplicates
    and skips rows, and the reader cannot tell — they just get a wrong list (`FRD-502` §4.2).
    """
    with _client() as client:
        _fill(client, *[_row(seconds_ago=i, model=f"m{i}") for i in range(6)])

        first = client.get("/v1beta/traces?limit=3").json()
        # Three newer rows arrive between the two requests.
        _fill(client, *[_row(seconds_ago=-i, model=f"new{i}") for i in range(1, 4)])
        second = client.get(f"/v1beta/traces?limit=3&cursor={first['next_cursor']}").json()

        seen = [r["id"] for r in first["traces"]] + [r["id"] for r in second["traces"]]
        assert len(seen) == len(set(seen)), "a row was shown twice"
        assert [r["model"] for r in second["traces"]] == ["m3", "m4", "m5"], "a row was skipped"


def test_two_rows_in_the_same_moment_still_page(sessions) -> None:
    """The cursor is a timestamp **and** an id: two rows can share a millisecond, and a timestamp
    alone would either repeat one or lose one."""
    same = NOW - timedelta(seconds=5)
    with _client() as client:
        _fill(client, *[_row(created_at=same, model=f"m{i}") for i in range(4)])

        first = client.get("/v1beta/traces?limit=2").json()
        second = client.get(f"/v1beta/traces?limit=2&cursor={first['next_cursor']}").json()

        seen = [r["id"] for r in first["traces"]] + [r["id"] for r in second["traces"]]
        assert len(seen) == len(set(seen)) == 4


def test_a_malformed_cursor_says_so() -> None:
    with _client() as client:
        response = client.get("/v1beta/traces?cursor=yesterday")

    assert response.status_code == 400
    assert "cursor" in response.json()["error"]["message"]


def test_the_page_size_is_bounded() -> None:
    """An unbounded page invites a caller who mistyped a number to ask for the whole table."""
    with _client() as client:
        assert client.get("/v1beta/traces?limit=100000").status_code == 422


# ---- what an incident needs, and who may see it (2026-08-08) ---------------------------------
#
# "Find a compromised client or system as fast as possible" is the question this view exists for.
# Three columns answer it — which system (`credential`, the API key prefix), which machine
# (`source_ip`), whose identity (`subject`) — and a fourth says what the model was asked to *do*.


def _incident() -> Principal:
    return Principal(subject="sec", method="oidc", roles=("it-security",))


def _oversight_caller() -> Principal:
    return Principal(subject="gov", method="oidc", roles=("it-steuerung",))


def _member_caller() -> Principal:
    return Principal(subject="alice", method="oidc", use_cases=("uc-a",))


def test_tool_calls_are_on_the_trace() -> None:
    """The most-asked question of this view: a governed *model* is evidenced by tokens and cost, a
    governed *agent* by what it tried to do."""
    with _client(_incident()) as client:
        _fill(client, _row(tool_calls={"declared": 3, "called": ["read_file"]}))
        rows = client.get("/v1beta/traces").json()["traces"]

    assert rows[0]["tool_calls"] == {"declared": 3, "called": ["read_file"]}


def test_only_the_turns_where_the_model_asked_for_something() -> None:
    with _client(_incident()) as client:
        _fill(
            client, _row(tool_calls={"declared": 1, "called": ["read_file"]}), _row(seconds_ago=1)
        )
        rows = client.get("/v1beta/traces?tools_only=true").json()["traces"]

    assert len(rows) == 1
    assert rows[0]["tool_calls"]["called"] == ["read_file"]


def test_an_incident_role_sees_the_source_address() -> None:
    with _client(_incident()) as client:
        _fill(client, _row(source_ip="203.0.113.9"))
        rows = client.get("/v1beta/traces").json()["traces"]

    assert rows[0]["source_ip"] == "203.0.113.9"


def test_everybody_else_does_not() -> None:
    """`source_ip` is the first question of an incident and personal data the rest of the time. An
    oversight role gets every column but that one."""
    with _client(_oversight_caller()) as client:
        _fill(client, _row(source_ip="203.0.113.9"))
        rows = client.get("/v1beta/traces").json()["traces"]

    assert rows
    assert "source_ip" not in rows[0]


def test_filtering_by_address_is_refused_rather_than_ignored() -> None:
    """A filter that silently does nothing lets somebody conclude an address made no requests —
    the opposite of what they were told."""
    with _client(_oversight_caller()) as client:
        _fill(client, _row(source_ip="203.0.113.9"))
        status = client.get("/v1beta/traces?source_ip=203.0.113.9").status_code

    assert status == 403


def test_an_incident_role_follows_one_system_across_use_cases() -> None:
    """The credential is an API key **prefix** — the public half — and it identifies a calling
    *system* rather than a person. Following it is how a compromised integration is isolated."""
    with _client(_incident()) as client:
        _fill(
            client,
            _row(credential="abcd1234", use_case="uc-a"),
            _row(credential="abcd1234", use_case="uc-b", seconds_ago=1),
            _row(credential="different", seconds_ago=2),
        )
        rows = client.get("/v1beta/traces?credential=abcd1234").json()["traces"]

    assert len(rows) == 2
    assert {row["use_case"] for row in rows} == {"uc-a", "uc-b"}


def test_only_my_own_requests() -> None:
    """Offered to every role, including the ones that see everything: an administrator checking
    what *they* did should not have to read past everybody else."""
    caller = Principal(subject="alice", method="oidc", roles=("it-security",))
    with _client(caller) as client:
        _fill(client, _row(subject="alice"), _row(subject="grace", seconds_ago=1))
        rows = client.get("/v1beta/traces?mine=true").json()["traces"]

    assert [row["subject"] for row in rows] == ["alice"]


def test_a_filter_cannot_widen_the_scope() -> None:
    """**The property that matters most here.** Every filter narrows; none may reach a use case the
    caller cannot see — a filter that bypassed the scope would be a tenant boundary with a query
    parameter for a door."""
    with _client(_member_caller()) as client:
        _fill(client, _row(use_case="somebody-elses", credential="abcd1234"))
        rows = client.get("/v1beta/traces?credential=abcd1234").json()["traces"]

    assert rows == []
