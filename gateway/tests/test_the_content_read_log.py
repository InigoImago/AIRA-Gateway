"""Who read which stored content, for the platform roles (`FRD-622`).

A content read has been recorded since `FRD-505` FR-6 and could be read back by nobody. Two
properties are guarded here. The record keeps the roles the reader held at that moment, because
`incident` does not say which role read it. And the log is served to the three platform roles and
to nobody else, because it says who looked at whose requests across every use case.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.incidents.content_reads import READ_FIELDS
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import PayloadAccess, RequestLog, UseCaseMemberRead, UseCaseRead

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

GLOBAL_ADMIN = Principal(subject="root", method="oidc", username="admin", roles=("global-admin",))
IT_SECURITY = Principal(subject="sec", method="oidc", username="itsec", roles=("it-security",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", username="itgov", roles=("it-steuerung",))
UC_ADMIN = Principal(subject="boss", method="oidc", use_cases=("uc-a",))
MEMBER = Principal(subject="alice", method="oidc", use_cases=("uc-a",))


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def _run(client: TestClient, work) -> object:  # noqa: ANN001 — an async callable
    with anyio.from_thread.start_blocking_portal() as portal:
        return portal.call(work)


def _seed_request(client: TestClient) -> None:
    async def seed() -> None:
        async with client.app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc-a", name="A", store_payloads=True))
            session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="boss", role="admin"))
            session.add(
                RequestLog(
                    id="row-1",
                    subject="alice",
                    auth_method="api_key",
                    use_case="uc-a",
                    api="gemini",
                    operation="generateContent",
                    model="mock-1",
                    status=200,
                    outcome="served",
                    created_at=NOW,
                    request_payload={"contents": [{"parts": [{"text": "hallo"}]}]},
                    response_payload={"candidates": []},
                )
            )
            await session.commit()

    _run(client, seed)


def _seed_reads(client: TestClient, *reads: PayloadAccess) -> None:
    async def seed() -> None:
        async with client.app.state.db_sessionmaker() as session:
            session.add_all(reads)
            await session.commit()

    _run(client, seed)


def _read(
    number: int, *, use_case: str = "uc-a", username: str | None = "admin", roles: str | None = ""
) -> PayloadAccess:
    return PayloadAccess(
        id=f"read-{number}",
        created_at=NOW + timedelta(minutes=number),
        request_log_id=f"row-{number}",
        use_case=use_case,
        subject=f"sub-{number}",
        username=username,
        ground="incident",
        roles=roles,
    )


# ═══ the record keeps the roles held then (FR-3) ════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("principal", "roles"),
    [(GLOBAL_ADMIN, "global-admin"), (IT_SECURITY, "it-security"), (UC_ADMIN, "")],
    ids=["global-admin", "it-security", "use-case-admin"],
)
def test_a_content_read_records_the_roles_held_at_that_moment(
    principal: Principal, roles: str
) -> None:
    """`incident` alone does not say whether a Global Administrator or IT Security read it, and a
    role held today is not evidence of the role held then. A reader with no organisation-wide role
    records an empty list, which is not the NULL of a read from before this was kept."""
    with _client(principal) as client:
        _seed_request(client)
        response = client.get("/v1beta/traces/row-1/payload")

        async def recorded() -> list[PayloadAccess]:
            async with client.app.state.db_sessionmaker() as session:
                return list((await session.execute(select(PayloadAccess))).scalars())

        rows = _run(client, recorded)

    assert response.status_code == 200, response.text
    assert [row.roles for row in rows] == [roles]  # type: ignore[attr-defined]


# ═══ the log is for the platform roles (FR-4) ═══════════════════════════════════════════════════


@pytest.mark.parametrize(
    "principal",
    [GLOBAL_ADMIN, IT_SECURITY, IT_STEUERUNG],
    ids=["global-admin", "it-security", "it-steuerung"],
)
def test_the_log_is_served_to_each_platform_role(principal: Principal) -> None:
    """IT Steuerung reads no content and may read the log: it is metadata about reads."""
    with _client(principal) as client:
        _seed_reads(client, _read(1, roles="global-admin"))
        response = client.get("/v1beta/content-reads")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["reads"]] == ["read-1"]


@pytest.mark.parametrize("principal", [UC_ADMIN, MEMBER], ids=["use-case-admin", "member"])
def test_the_log_is_refused_to_everybody_else(principal: Principal) -> None:
    """The log says who looked at whose requests across every use case. An administrator of one
    use case is not entitled to that, nor is a member."""
    with _client(principal) as client:
        _seed_reads(client, _read(1))
        response = client.get("/v1beta/content-reads")

    assert response.status_code == 403
    assert (
        "Global Administrators, IT Security and IT Steuerung" in response.json()["error"]["message"]
    )


def test_the_log_lists_newest_first_and_only_what_it_allows() -> None:
    """An allow-list, so a column added to `payload_access` does not appear because nobody excluded
    it. Roles come back as a list, and a read from before they were kept as `null`."""
    with _client(GLOBAL_ADMIN) as client:
        _seed_reads(
            client,
            _read(1, roles=None),
            _read(2, roles="global-admin,it-security"),
            _read(3, roles=""),
        )
        body = client.get("/v1beta/content-reads").json()

    reads = body["reads"]
    assert [row["id"] for row in reads] == ["read-3", "read-2", "read-1"]
    assert all(set(row) == set(READ_FIELDS) for row in reads)
    assert [row["roles"] for row in reads] == [[], ["global-admin", "it-security"], None]
    assert body["next_cursor"] is None


def test_the_log_filters_by_use_case_and_by_reader() -> None:
    """The reader filter matches the name or the subject: a read by an API key carries no name."""
    with _client(IT_SECURITY) as client:
        _seed_reads(
            client,
            _read(1, use_case="uc-a", username="admin"),
            _read(2, use_case="uc-b", username="itsec"),
            _read(3, use_case="uc-b", username=None),
        )
        by_use_case = client.get("/v1beta/content-reads", params={"use_case": "uc-b"}).json()
        by_name = client.get("/v1beta/content-reads", params={"reader": "admin"}).json()
        by_subject = client.get("/v1beta/content-reads", params={"reader": "sub-3"}).json()

    assert [row["id"] for row in by_use_case["reads"]] == ["read-3", "read-2"]
    assert [row["id"] for row in by_name["reads"]] == ["read-1"]
    assert [row["id"] for row in by_subject["reads"]] == ["read-3"]


def test_the_log_pages_by_cursor_without_repeating_or_skipping() -> None:
    with _client(GLOBAL_ADMIN) as client:
        _seed_reads(client, _read(1), _read(2), _read(3))
        first = client.get("/v1beta/content-reads", params={"limit": 2}).json()
        second = client.get(
            "/v1beta/content-reads", params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()

    assert [row["id"] for row in first["reads"]] == ["read-3", "read-2"]
    assert [row["id"] for row in second["reads"]] == ["read-1"]
    assert second["next_cursor"] is None
