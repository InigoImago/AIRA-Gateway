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

#: The per-use-case ladder, which decides while strict data mode is off (`FRD-627`).
pytestmark = pytest.mark.usefixtures("strict_data_off")

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
    visibility: str = "members",
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
                payload_visibility=visibility,
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
# Seven readers against the three ways a stored use case can answer, as a **table keyed by both**
# rather than a list of cases. A list is where a new column gets added for the rows somebody
# thought of: `oversight` arrived on 2026-10-11 and a list would have grown four of its seven
# cells, all of them the interesting ones. The completeness check below fails on a missing cell,
# so a fourth visibility or a fifth role is a diff that does not compile until it is answered.
#
# `expected` is the HTTP status; `reason` is the refusal in a 200 body or the 403's message;
# `ground` is the authority a granted read is recorded under.

#: Every reader this decision distinguishes, by the name its row carries.
READERS = {
    "global-admin": GLOBAL_ADMIN,
    "it-security": IT_SECURITY,
    "it-steuerung": IT_STEUERUNG,
    "uc-admin": UC_ADMIN,
    "own-request": UC_USER,
    "colleagues-request": OTHER_USER,
    "outsider": OUTSIDER,
}

#: The three answers a use case that **stores** content can give (`FRD-622` FR-1). "Not stored" is
#: not here: it is the other question — whether anything is written — and it has its own cases.
VISIBILITIES = ("members", "own", "oversight")

#: `(reader, visibility)` → `(status, reason, ground)`.
#:
#: Read it as sentences. An incident role reads anything, which is the point of the role. IT
#: Steuerung reads every figure and no content, in every visibility. A use-case administrator reads
#: their use case — **except** where the use case has left content to oversight, which is the whole
#: of that mode: the administrator is not an oversight role, and a mode that let them read would
#: promise something it cannot keep. An outsider is told 404, because 403 would confirm the use
#: case exists.
EXPECTED: dict[tuple[str, str], tuple[int, str | None, str | None]] = {
    ("global-admin", "members"): (200, None, "incident"),
    ("global-admin", "own"): (200, None, "incident"),
    ("global-admin", "oversight"): (200, None, "incident"),
    ("it-security", "members"): (200, None, "incident"),
    ("it-security", "own"): (200, None, "incident"),
    ("it-security", "oversight"): (200, None, "incident"),
    ("it-steuerung", "members"): (403, "not_a_content_role", None),
    ("it-steuerung", "own"): (403, "not_a_content_role", None),
    ("it-steuerung", "oversight"): (403, "not_a_content_role", None),
    ("uc-admin", "members"): (200, None, "use_case_admin"),
    ("uc-admin", "own"): (200, None, "use_case_admin"),
    ("uc-admin", "oversight"): (403, "oversight_only", None),
    ("own-request", "members"): (200, None, "use_case_member"),
    ("own-request", "own"): (200, None, "use_case_member"),
    ("own-request", "oversight"): (403, "oversight_only", None),
    ("colleagues-request", "members"): (200, None, "use_case_member"),
    ("colleagues-request", "own"): (403, "others_request", None),
    ("colleagues-request", "oversight"): (403, "oversight_only", None),
    ("outsider", "members"): (404, None, None),
    ("outsider", "own"): (404, None, None),
    ("outsider", "oversight"): (404, None, None),
}


def test_the_matrix_answers_every_reader_in_every_visibility() -> None:
    """The table is the specification, so a hole in it is a sentence nobody wrote.

    Both directions: a cell for a reader or a visibility that no longer exists is a claim about
    nothing, and it reads as coverage.
    """
    want = {(reader, visibility) for reader in READERS for visibility in VISIBILITIES}

    assert set(EXPECTED) == want, (
        "unanswered: "
        f"{sorted(want - set(EXPECTED))}; stale: {sorted(set(EXPECTED) - want)}. Every reader has "
        "an answer in every visibility, and a missing one is the case somebody will discover in "
        "production."
    )


#: A **status is not an answer.** Three different refusals all return 403, so a matrix that checked
#: only the number would pass with any two of them swapped — and it did: an audit that broke each
#: branch of `payloads.py` in turn found `is_oversight` **undefended**, because removing it makes an
#: oversight role fall through to `OUT_OF_SCOPE`, which is also a 403. The distinction is the whole
#: point of the message: "you see figures, not content" and "that use case is not yours" send the
#: reader to two different people.
@pytest.mark.parametrize(
    ("reader", "visibility"),
    [
        pytest.param(reader, visibility, id=f"{reader}-{visibility}")
        for reader in READERS
        for visibility in VISIBILITIES
    ],
)
def test_who_may_read_a_stored_prompt(reader: str, visibility: str) -> None:
    expected, reason, ground = EXPECTED[(reader, visibility)]
    with _client(READERS[reader]) as client:
        _fill(client, visibility=visibility)
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
        _fill(client, visibility="own")
        rows = client.get("/v1beta/traces").json()["traces"]

    assert rows == []


def test_the_same_user_sees_the_row_when_the_use_case_does_not_restrict() -> None:
    with _client(OTHER_USER) as client:
        _fill(client, visibility="members")
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1


def test_an_administrator_of_a_restricted_use_case_still_sees_every_row() -> None:
    """The restriction is on its *users*. An administrator who could no longer see their own use
    case's traffic could not administer it."""
    with _client(UC_ADMIN) as client:
        _fill(client, visibility="own")
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
        _fill(client, visibility="own")
        rows = client.get("/v1beta/traces").json()["traces"]

    assert len(rows) == 1, (
        "an administrator was read as a plain member, so the use case's own restriction was "
        "applied to the person who administers it"
    )


def test_an_incident_role_is_never_restricted() -> None:
    with _client(IT_SECURITY) as client:
        _fill(client, visibility="own")
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
        session.add(UseCaseRead(slug="uc-a", name="A", payload_visibility="own"))
        await session.commit()

    async with sessions() as session:
        assert await restricted_use_cases(session, GROUP_ADMIN, strict_data=False) == []


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
        session.add(UseCaseRead(slug="uc-a", name="A", payload_visibility="own"))
        await session.commit()

    async with sessions() as session:
        assert await restricted_use_cases(session, plain, strict_data=False) == ["uc-a"]


# ═══ oversight only: stored, and read by nobody in the use case ═════════════════════════════════
#
# The fourth answer the owner asked for (`FRD-622` FR-1b). Its two halves are easy to get wrong in
# opposite directions: a mode that hid the *rows* as well would take a use case's own operations
# view away, and a mode that let the use case's administrator read content would promise something
# it cannot keep. Both are asserted here, beside what the mode does to the evidence.


def test_the_refusal_says_who_can_read_and_that_the_reading_is_recorded() -> None:
    """A dead end is not an answer. Somebody who needs that prompt — for a complaint, an incident,
    a subject access request — has to learn from the refusal **who** can get it for them, and the
    person whose data it is has to learn that such a read leaves a record."""
    with _client(UC_ADMIN) as client:
        _fill(client, visibility="oversight")
        response = client.get("/v1beta/traces/row-1/payload")

    message = response.json()["error"]["message"]
    assert response.status_code == 403
    assert "Global Administrator" in message and "IT Security" in message
    assert "recorded" in message
    # And it names who chose this, because the next question is always "can that be changed".
    assert "administrator chose" in message


def test_an_oversight_read_is_recorded_like_any_other() -> None:
    """`ADR-0016`'s bargain: content is readable **because** every read is written down. The mode
    that narrows reading to two roles is the one where that record matters most — it is the only
    trace of the only people who can look."""
    with _client(IT_SECURITY) as client:
        _fill(client, visibility="oversight")
        response = client.get("/v1beta/traces/row-1/payload")
        records = _accesses(client)

    assert response.status_code == 200
    assert "4711" in str(response.json()["request"])
    assert [(record.request_log_id, record.ground) for record in records] == [("row-1", "incident")]


def test_a_read_this_mode_refuses_records_nothing_either() -> None:
    """The rule above, for the refusal this mode adds: the record is of **disclosures**. Asserted
    again rather than assumed, because this refusal is the first one that turns away somebody who
    is otherwise entitled — an administrator of the use case — and an entitled caller is exactly
    who a *"record the attempt"* exception would be written for."""
    with _client(UC_ADMIN) as client:
        _fill(client, visibility="oversight")
        refused = client.get("/v1beta/traces/row-1/payload")
        records = _accesses(client)

    assert refused.status_code == 403
    assert records == []


@pytest.mark.parametrize(
    "reader", ["uc-admin", "colleagues-request"], ids=["uc-admin", "a-colleague"]
)
def test_oversight_hides_the_content_and_leaves_the_rows(reader: str) -> None:
    """**FR-1c, in both directions.** A use case still has to be able to run itself: what failed,
    what it cost, who is calling too much. That is the row. The prompt is not, and this mode is
    about the prompt — so the row is there and the content behind it is not.

    Asserted for a colleague as well as the administrator, because `own` restricts exactly this
    list and a mode that leaked into `restricted_use_cases` would silently take the colleague's
    rows away too.
    """
    with _client(READERS[reader]) as client:
        _fill(client, visibility="oversight")
        rows = client.get("/v1beta/traces").json()["traces"]
        content = client.get("/v1beta/traces/row-1/payload")

    assert len(rows) == 1, "the mode took away the operations view, which is not its business"
    assert rows[0]["model"] == "mock-1"
    assert content.status_code == 403


def test_the_mode_is_not_a_second_way_to_say_not_stored() -> None:
    """Three facts, three answers (the rule this file opens with): *nothing was written*, *it was
    written and you may not read it*, and *it was written and has expired*. A reader who cannot
    tell them apart cannot tell a setting from a clock from a boundary — and for this mode the
    difference decides whether asking IT Security is worth anything."""
    with _client(UC_ADMIN) as client:
        _fill(client, visibility="oversight", store_payloads=False, payload=False)
        not_stored = client.get("/v1beta/traces/row-1/payload")

    # Authority first: the use case has left content to oversight, so that is what its own
    # administrator is told — before anything says whether a payload exists. Telling them "not
    # stored" would be a disclosure about the data by somebody who may not see the data.
    assert not_stored.status_code == 403
    assert MESSAGES[PayloadRefusal.OVERSIGHT_ONLY] in not_stored.json()["error"]["message"]

    with _client(IT_SECURITY) as client:
        _fill(client, visibility="oversight", store_payloads=False, payload=False)
        oversight = client.get("/v1beta/traces/row-1/payload")

    # The role that may read is told the truth about the data: there is none.
    assert oversight.status_code == 200
    assert oversight.json()["reason"] == "not_stored"


@pytest.mark.parametrize(
    ("operation", "outcome", "use_case"),
    [
        ("pipeline:injection_filter", "served", "uc-a"),
        ("generateContent", "diagnostic", None),
    ],
    ids=["a-pipeline-step", "a-model-check"],
)
def test_a_call_the_gateway_made_itself_is_not_reported_as_expired(
    operation: str, outcome: str, use_case: str | None
) -> None:
    """A pipeline step's model call and a model check are answered and never keep their text — by
    design, not by a clock. "Stored and expired" about them is a false statement about retention,
    and FR-7 is that each absence gets its own answer."""

    async def seed(sessionmaker) -> None:  # noqa: ANN001
        async with sessionmaker() as session:
            session.add(UseCaseRead(slug="uc-a", name="A", store_payloads=True))
            session.add(
                RequestLog(
                    id="own-call",
                    subject="sec",
                    auth_method="oidc",
                    use_case=use_case,
                    api="gemini",
                    operation=operation,
                    model="mock-1",
                    status=200,
                    outcome=outcome,
                )
            )
            await session.commit()

    with _client(IT_SECURITY) as client:
        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(seed, client.app.state.db_sessionmaker)
        body = client.get("/v1beta/traces/own-call/payload").json()

    assert body["available"] is False
    assert body["reason"] == "never_kept"
    assert "expired" not in body["message"]
