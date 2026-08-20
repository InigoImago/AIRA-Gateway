"""A person's own request is theirs whichever credential made it (`FRD-606`, `FRD-505`).

The two credentials answer *who is this* in different alphabets — an API key's subject **is** its
owner's username, an OIDC token's is the directory's user id — and `aira_gateway.scopes.person`
exists because of it: a per-head budget, a rate-limit bucket and a use-case membership are all
keyed on the *person*, so a key and a browser session by the same human meet in one place.

Three reads were still keyed on the raw subject:

    payloads._authority        "restricted to your own requests" on the payload
    /v1beta/traces             the same restriction, applied to the list
    /v1beta/traces?mine=true   "only my own requests", offered to every role

The console is always OIDC and the traffic is usually an API key, so the comparison was between a
directory id and a username and could never match. Measured before this test existed: a member of
a use case that restricts members to their own requests saw an **empty** trace list with their own
requests in the table, and `403 others_request` on their own prompt.

The setup is the whole point — a `Principal` whose `subject` and `username` differ, which is what
every real OIDC caller looks like and what no test in this suite had.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import RequestLog, UseCaseMemberRead, UseCaseRead

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
PROMPT = {"contents": [{"parts": [{"text": "the customer number is 4711"}]}]}

#: What every real console caller looks like: the directory's id, and the name beside it.
SIGNED_IN = Principal(
    subject="8f3c-0d21-uuid", method="oidc", username="alice", use_cases=("uc-a",)
)
COLLEAGUE = Principal(subject="1a2b-3c4d-uuid", method="oidc", username="bob", use_cases=("uc-a",))


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


async def _seed(sessions, *, restricted: bool) -> None:
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A", restrict_members_to_own_requests=restricted))
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="alice", role="user"))
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="bob", role="user"))
        # Alice's own traffic, made with **her API key**: the subject is her username.
        session.add(
            RequestLog(
                id="row-key",
                subject="alice",
                username="alice",
                auth_method="api_key",
                use_case="uc-a",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                created_at=NOW,
                request_payload=PROMPT,
                response_payload={"text": "ok"},
            )
        )
        # A colleague's, so the restriction still has something to withhold.
        session.add(
            RequestLog(
                id="row-other",
                subject="bob",
                username="bob",
                auth_method="api_key",
                use_case="uc-a",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                created_at=NOW,
                request_payload=PROMPT,
                response_payload={"text": "ok"},
            )
        )
        await session.commit()


def _fill(client: TestClient, *, restricted: bool) -> None:
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: _seed(client.app.state.db_sessionmaker, restricted=restricted))


def test_a_restricted_member_sees_the_requests_their_key_made() -> None:
    with _client(SIGNED_IN) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert [row["id"] for row in rows] == ["row-key"], (
        "the caller signed in as the person who made this request and was shown nothing"
    )


def test_mine_finds_the_requests_this_person_made() -> None:
    with _client(SIGNED_IN) as client:
        _fill(client, restricted=False)
        rows = client.get("/v1beta/traces?mine=true").json()["traces"]

    assert [row["id"] for row in rows] == ["row-key"]


def test_a_restricted_member_may_read_their_own_prompt() -> None:
    with _client(SIGNED_IN) as client:
        _fill(client, restricted=True)
        response = client.get("/v1beta/traces/row-key/payload")

    assert response.status_code == 200, response.text
    assert "4711" in str(response.json()["request"])


def test_the_restriction_still_withholds_a_colleagues_request() -> None:
    """The widening must not become an opening: the whole rule is *own*, not *any*."""
    with _client(SIGNED_IN) as client:
        _fill(client, restricted=True)
        response = client.get("/v1beta/traces/row-other/payload")

    assert response.status_code == 403, response.text
    assert "own requests only" in response.json()["error"]["message"]


def test_a_colleague_still_sees_only_their_own() -> None:
    with _client(COLLEAGUE) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert [row["id"] for row in rows] == ["row-other"]
