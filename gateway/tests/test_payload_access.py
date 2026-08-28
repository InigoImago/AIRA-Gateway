"""Who may read a stored prompt — the whole matrix, in one table (`FRD-505`).

Written as a matrix on purpose. Four roles, a per-use-case restriction, a storage switch and a
retention clock interact, and prose reasoning about "IT Steuerung, in a restricted use case, on
somebody else's request, where storage is off" is reasoning nobody can check. The table below is
the specification; each row is one sentence a person can read and disagree with.

The three refusals that are *not* about authority get a **200 with a reason**, and the tests assert
which reason. "This use case does not store payloads", "it stored them and they expired" and "this
request never had one" describe three different installations, and a view that answers all three
with "not available" teaches its reader to distrust it — the same mistake `FRD-502` had to undo
between "nothing happened" and "you see nothing".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import PayloadAccess, RequestLog, UseCaseMemberRead, UseCaseRead
from aira_gateway.payloads import MESSAGES, PayloadRefusal

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
PROMPT = {"contents": [{"parts": [{"text": "the customer number is 4711"}]}]}
ANSWER = {"candidates": [{"content": "acknowledged"}]}

GLOBAL_ADMIN = Principal(subject="root", method="oidc", roles=("global-admin",))
IT_SECURITY = Principal(subject="sec", method="oidc", roles=("it-security",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
UC_ADMIN = Principal(subject="boss", method="oidc", roles=("use-case-admin",), use_cases=("uc-a",))
UC_USER = Principal(subject="alice", method="oidc", roles=("use-case-user",), use_cases=("uc-a",))
OTHER_USER = Principal(subject="bob", method="oidc", roles=("use-case-user",), use_cases=("uc-a",))
OUTSIDER = Principal(subject="eve", method="oidc", roles=("use-case-user",), use_cases=("uc-z",))


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


async def _seed(
    sessions,
    *,
    store_payloads: bool = True,
    restricted: bool = False,
    payload: bool = True,
    status: int = 200,
    row_id: str = "",
) -> None:
    async with sessions() as session:
        session.add(
            UseCaseRead(
                slug="uc-a",
                name="A",
                store_payloads=store_payloads,
                restrict_members_to_own_requests=restricted,
            )
        )
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="boss", role="admin"))
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="alice", role="user"))
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="bob", role="user"))
        session.add(
            RequestLog(
                id=row_id or "row-1",
                subject="alice",
                auth_method="api_key",
                use_case="uc-a",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=status,
                outcome="served" if status < 300 else "invalid_request",
                created_at=NOW,
                request_payload=PROMPT if payload else None,
                response_payload=ANSWER if payload else None,
            )
        )
        await session.commit()


def _fill(client: TestClient, **kwargs) -> None:
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: _seed(client.app.state.db_sessionmaker, **kwargs))


def _accesses(client: TestClient) -> list[PayloadAccess]:
    async def read() -> list[PayloadAccess]:
        async with client.app.state.db_sessionmaker() as session:
            return list((await session.execute(select(PayloadAccess))).scalars())

    with anyio.from_thread.start_blocking_portal() as portal:
        return portal.call(read)


# ═══ the matrix ═════════════════════════════════════════════════════════════════════════════════
#
# `expected` is the HTTP status; `reason` is the refusal name in a 200 body, or None when content
# is expected.


#: A **status is not an answer.** Three different refusals all return 403, so a matrix that checked
#: only the number would pass with any two of them swapped — and it did: an audit that broke each
#: branch of `payloads.py` in turn found `is_oversight` **undefended**, because removing it makes an
#: oversight role fall through to `OUT_OF_SCOPE`, which is also a 403. The distinction is the whole
#: point of the message: "you see figures, not content" and "that use case is not yours" send the
#: reader to two different people.
@pytest.mark.parametrize(
    ("principal", "restricted", "expected", "reason", "ground"),
    [
        # An incident role reads anything, restriction or not — that is the point of the role.
        (GLOBAL_ADMIN, False, 200, None, "incident"),
        (GLOBAL_ADMIN, True, 200, None, "incident"),
        (IT_SECURITY, False, 200, None, "incident"),
        (IT_SECURITY, True, 200, None, "incident"),
        # Every figure, no content. The split this whole feature turns on — and the *reason* is
        # asserted, not just the status, or this row passes while the role boundary is gone.
        (IT_STEUERUNG, False, 403, "not_a_content_role", None),
        (IT_STEUERUNG, True, 403, "not_a_content_role", None),
        # The use case's own administrator, including under their own restriction.
        (UC_ADMIN, False, 200, None, "use_case_admin"),
        (UC_ADMIN, True, 200, None, "use_case_admin"),
        # A user reading their own request: always allowed.
        (UC_USER, False, 200, None, "use_case_member"),
        (UC_USER, True, 200, None, "use_case_member"),
        # A user reading a colleague's: allowed until the administrator restricts it.
        (OTHER_USER, False, 200, None, "use_case_member"),
        (OTHER_USER, True, 403, "others_request", None),
        # Somebody else's use case entirely. 404, because saying 403 would confirm it exists.
        (OUTSIDER, False, 404, None, None),
    ],
    ids=[
        "global-admin",
        "global-admin-restricted",
        "it-security",
        "it-security-restricted",
        "it-steuerung-refused",
        "it-steuerung-refused-restricted",
        "uc-admin",
        "uc-admin-restricted",
        "own-request",
        "own-request-restricted",
        "colleagues-request",
        "colleagues-request-restricted",
        "outsider",
    ],
)
def test_who_may_read_a_stored_prompt(principal, restricted, expected, reason, ground) -> None:
    with _client(principal) as client:
        _fill(client, restricted=restricted)
        response = client.get("/v1beta/traces/row-1/payload")

    assert response.status_code == expected, response.text
    body = response.json()
    if expected == 403:
        # The sentence, not only the number. Every authority refusal is a 403, and which one it is
        # decides who the reader goes and talks to.
        assert MESSAGES[PayloadRefusal(reason)] in body["error"]["message"]
        return
    if expected != 200:
        return
    if reason is None:
        assert body["available"] is True
        assert "4711" in str(body["request"]), "the reader was allowed and got nothing"
        assert body["ground"] == ground
    else:
        assert body["available"] is False
        assert body["reason"] == reason


@pytest.mark.parametrize(
    ("store_payloads", "payload", "status", "reason"),
    [
        (False, False, 200, "not_stored"),
        (True, False, 200, "expired"),
        (True, False, 400, "never_had_one"),
    ],
    ids=["storage-off", "retention-removed-it", "refused-before-dispatch"],
)
def test_three_ways_to_have_nothing_are_three_different_answers(
    store_payloads, payload, status, reason
) -> None:
    """A single "not available" would leave the reader unable to tell a *setting* from a *clock*
    from a request that never reached a model. Two of those are somebody's to change."""
    with _client(IT_SECURITY) as client:
        _fill(client, store_payloads=store_payloads, payload=payload, status=status)
        response = client.get("/v1beta/traces/row-1/payload")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == reason
    assert body["message"], "a reason with no sentence is a code the reader cannot act on"


# ═══ the record that makes the permission defensible ════════════════════════════════════════════


def test_reading_a_prompt_is_itself_recorded() -> None:
    """`ADR-0009` refused this view because it shows content across use-case boundaries. What
    reopened it is that the act leaves a trail — so the trail is the feature, not a log line."""
    with _client(IT_SECURITY) as client:
        _fill(client)
        assert client.get("/v1beta/traces/row-1/payload").status_code == 200
        rows = _accesses(client)

    assert len(rows) == 1
    assert rows[0].subject == "sec"
    assert rows[0].request_log_id == "row-1"
    assert rows[0].use_case == "uc-a"
    assert rows[0].ground == "incident", "the authority the read rested on was not recorded"


def test_a_refused_read_records_nothing() -> None:
    """An access log that filled up with attempts nobody was granted would make the real reads
    harder to find, and the attempt is already a 403 in the request log."""
    with _client(IT_STEUERUNG) as client:
        _fill(client)
        assert client.get("/v1beta/traces/row-1/payload").status_code == 403
        assert _accesses(client) == []


def test_every_read_is_recorded_not_only_the_first() -> None:
    """Two reads of the same prompt are two events. Deduplicating would answer "who has seen this"
    correctly and "how often was it looked at" wrongly, and the second is the one an unusual
    pattern shows up in."""
    with _client(GLOBAL_ADMIN) as client:
        _fill(client)
        client.get("/v1beta/traces/row-1/payload")
        client.get("/v1beta/traces/row-1/payload")
        assert len(_accesses(client)) == 2


def test_an_unknown_request_is_not_found_rather_than_forbidden() -> None:
    with _client(GLOBAL_ADMIN) as client:
        _fill(client)
        response = client.get(f"/v1beta/traces/{uuid.uuid4()}/payload")

    assert response.status_code == 404


# ═══ the list obeys the same restriction as the content ═════════════════════════════════════════


def test_a_restricted_user_does_not_even_see_the_row() -> None:
    """Withholding only the content would still disclose who else calls, how often and at what
    cost — the interesting half — and would read as a broken screen rather than a boundary."""
    with _client(OTHER_USER) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert rows == []


def test_the_same_user_sees_the_row_when_the_use_case_does_not_restrict() -> None:
    with _client(OTHER_USER) as client:
        _fill(client, restricted=False)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1


def test_an_administrator_of_a_restricted_use_case_still_sees_every_row() -> None:
    """The restriction is on its *users*. An administrator who could no longer see their own use
    case's traffic could not administer it."""
    with _client(UC_ADMIN) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1


def test_an_administrator_is_recognised_when_the_token_carries_a_username() -> None:
    """**The alphabet `use_case_members` is keyed in** (2026-08-15).

    Management emits `{"username": …}` on `membership.upserted` and the consumer writes it into
    `UseCaseMemberRead.subject` — so the column called `subject` holds a **name**. `auth/grants.py`
    reads it against `principal.username`; `payloads.py` read it against `principal.subject`, which
    for an OIDC token is the directory's user id. The two never matched, so every console user was
    resolved as a plain member: an administrator of a restricted use case could not see their own
    use case's traffic, contradicting the test above and the switch's own help text.

    It survived because the principals above carry **no username**, which makes `person()` fall
    back to the subject and the two coincide — the fixture never reached the path it was named
    after. A real Keycloak token looks like this one.
    """
    signed_in = Principal(
        subject="3f7c1a20-0f2b-4a1e-9a11-b3c0d5e6f701",  # what Keycloak actually puts in `sub`
        method="oidc",
        username="boss",  # and the name the membership was written with
        use_cases=("uc-a",),
    )

    with _client(signed_in) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1, (
        "an administrator was read as a plain member, so the use case's own restriction was "
        "applied to the person who administers it"
    )


def test_an_incident_role_is_never_restricted() -> None:
    with _client(IT_SECURITY) as client:
        _fill(client, restricted=True)
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1


# ═══ what the filter objected to (FRD-505 FR-5) ════════════════════════════════════════════════
#
# The screen's most-asked question, in the owner's words: "show me the prompts that threw a
# warning". Two things count as an objection and only one of them announces itself — a *blocked*
# request already fails visibly, while a *flagged* one is a 200 with a note attached that nobody
# would otherwise look at twice.


async def _seed_flagged(sessions) -> None:
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A"))
        for row_id, flagged, outcome, status in (
            ("clean", False, "served", 200),
            ("flagged", True, "served", 200),
            ("blocked", True, "blocked_by_pipeline", 400),
        ):
            session.add(
                RequestLog(
                    id=row_id,
                    subject="alice",
                    auth_method="api_key",
                    use_case="uc-a",
                    api="gemini",
                    operation="generateContent",
                    model="mock-1",
                    status=status,
                    outcome=outcome,
                    flagged=flagged,
                    created_at=NOW,
                )
            )
        await session.commit()


def test_the_requests_a_pipeline_step_objected_to_can_be_asked_for() -> None:
    with _client(IT_SECURITY) as client:
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(lambda: _seed_flagged(client.app.state.db_sessionmaker))
        rows = client.get("/v1beta/traces?flagged_only=true").json()["traces"]

    assert {row["id"] for row in rows} == {"flagged", "blocked"}


def test_a_flagged_request_that_was_served_is_still_flagged() -> None:
    """The one worth having. A blocked request is visible because it failed; a flagged one looks
    exactly like every other 200 until somebody asks this question."""
    with _client(IT_SECURITY) as client:
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(lambda: _seed_flagged(client.app.state.db_sessionmaker))
        rows = client.get("/v1beta/traces?flagged_only=true").json()["traces"]

    served = [row for row in rows if row["outcome"] == "served"]
    assert served and served[0]["flagged"] is True


def test_the_flag_is_on_the_row_so_the_table_can_show_it_without_asking_again() -> None:
    with _client(IT_SECURITY) as client:
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(lambda: _seed_flagged(client.app.state.db_sessionmaker))
        rows = client.get("/v1beta/traces").json()["traces"]

    assert {row["id"]: row["flagged"] for row in rows} == {
        "clean": False,
        "flagged": True,
        "blocked": True,
    }


# == an administrator is an administrator however the grant reached them ==========================
#
# `FRD-209` FR-6 is the owner's sentence: *"I want to add any group from Keycloak and any user as
# well, and give them admin or user rights — I do not want to have to make a group named after the
# use case."* Two of those three routes wrote a row in `use_case_members`, and this module read
# only that table — so the **group** route, the one the sentence leads with, produced an
# administrator the gateway treated as a plain member.


GROUP_ADMIN = Principal(
    subject="uuid-of-boss",
    method="oidc",
    username="boss",
    use_cases=("uc-a",),
    groups=("/ai/kundenservice",),
    grants=(("uc-a", "admin"),),
)


async def test_a_group_granted_administrator_is_read_as_an_administrator(sessions) -> None:
    """The role the resolver worked out, arriving where the decision is made.

    A grant on a group writes no row in `use_case_members` — that is what makes it a group grant —
    and `grant_role_in` read that table alone. Measured on 2026-08-26: `admin` on
    `/ai/kundenservice` for `uc-a` resolved to `"user"`.
    """
    from aira_gateway.payloads import grant_role_in

    async with sessions() as session:
        assert await grant_role_in(session, GROUP_ADMIN, "uc-a") == "admin"


async def test_a_member_row_and_a_group_grant_take_the_stronger_of_the_two(sessions) -> None:
    """Being granted twice over is being granted, and which source was read first is not a thing
    an access decision may depend on (`aira_common.access`)."""
    from aira_gateway.payloads import grant_role_in

    async with sessions() as session:
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject="boss", role="user"))
        await session.commit()

    async with sessions() as session:
        assert await grant_role_in(session, GROUP_ADMIN, "uc-a") == "admin"


async def test_a_group_grant_of_user_does_not_become_an_administrator(sessions) -> None:
    """The other direction — the fix must widen nothing it was not asked to widen."""
    from aira_gateway.payloads import grant_role_in

    plain = Principal(
        subject="uuid-of-alice",
        method="oidc",
        username="alice",
        use_cases=("uc-a",),
        groups=("/ai/kundenservice",),
        grants=(("uc-a", "user"),),
    )
    async with sessions() as session:
        assert await grant_role_in(session, plain, "uc-a") == "user"


async def test_a_restricted_use_case_does_not_narrow_a_group_granted_administrator(
    sessions,
) -> None:
    """The wider blast radius of the same omission: this decides the whole trace **list**, so the
    person who administers a use case was shown only their own traffic in it."""
    from aira_gateway.payloads import restricted_use_cases

    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A", restrict_members_to_own_requests=True))
        await session.commit()

    async with sessions() as session:
        assert await restricted_use_cases(session, GROUP_ADMIN) == []


async def test_a_restricted_use_case_still_narrows_a_group_granted_member(sessions) -> None:
    """And still narrows the reader it is for, or the fix has removed the control."""
    from aira_gateway.payloads import restricted_use_cases

    plain = Principal(
        subject="uuid-of-alice",
        method="oidc",
        username="alice",
        use_cases=("uc-a",),
        groups=("/ai/kundenservice",),
        grants=(("uc-a", "user"),),
    )
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A", restrict_members_to_own_requests=True))
        await session.commit()

    async with sessions() as session:
        assert await restricted_use_cases(session, plain) == ["uc-a"]
