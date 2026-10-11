"""Whose **figures** a reader sees follows the use case's content setting (`FRD-603` §5.5).

**The works council's second question, and for two months the answer was "it does not apply".** A
use case could withhold a colleague's prompt and still print, to every member, how many requests
that colleague made, what they cost and how long each took. The privacy notice said so — *"the
'own requests only' setting does not currently restrict these reports"* — which made it a stated
gap rather than a hidden one, and a stated gap is still a gap: latency and request counts per
person are exactly the material §87(1) no. 6 BetrVG is about.

So the ladder is the content's, one dimension lower, and it is about a **split by person**:

| `payload_visibility` | who sees other people's figures in that use case |
| --- | --- |
| `members` | everybody in the use case |
| `own` | administrators of the use case |
| `oversight` | nobody in the use case, its administrators included |

What stays in **every** mode, and is asserted here as carefully as what goes: the use case's
**totals**, every breakdown that is not keyed on a person, and each reader's own figures. A use
case has to be able to see what it costs and what fails. Roles that may read every figure are not
narrowed at all — they are the oversight the mode leaves content to.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import RequestLog, UseCaseMemberRead, UseCaseRead

WHEN = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
WINDOW = "?from=2026-10-01&to=2026-11-01"

#: Alice is a plain member, Boss administers the use case, and Gov reads every figure and no
#: content (IT Steuerung) — the three positions the ladder distinguishes.
ALICE = Principal(subject="alice", method="oidc", roles=("use-case-user",), use_cases=("uc",))
BOSS = Principal(subject="boss", method="oidc", roles=("use-case-user",), use_cases=("uc",))
GOV = Principal(subject="gov", method="oidc", roles=("it-steuerung",))

READERS = {"a-member": ALICE, "an-administrator": BOSS, "an-oversight-role": GOV}

#: `(reader, visibility)` → the person keys that reader may see. `None` means "everybody's".
#:
#: Keyed by both, with the completeness check below, for the reason the payload matrix is: a list
#: grows the cases somebody thought of, and the interesting cells here are the ones that take
#: something away from an administrator.
EXPECTED: dict[tuple[str, str], set[str] | None] = {
    ("a-member", "members"): None,
    ("a-member", "own"): {"alice"},
    ("a-member", "oversight"): {"alice"},
    ("an-administrator", "members"): None,
    ("an-administrator", "own"): None,
    ("an-administrator", "oversight"): {"boss"},
    ("an-oversight-role", "members"): None,
    ("an-oversight-role", "own"): None,
    ("an-oversight-role", "oversight"): None,
}

VISIBILITIES = ("members", "own", "oversight")


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


async def _seed(app, visibility: str) -> None:
    """One use case, two people who called it, and the grants that say who administers it."""
    async with app.state.db_sessionmaker() as session:
        session.add(
            UseCaseRead(slug="uc", name="uc", payload_visibility=visibility, store_payloads=True)
        )
        session.add(UseCaseMemberRead(use_case_slug="uc", subject="boss", role="admin"))
        session.add(UseCaseMemberRead(use_case_slug="uc", subject="alice", role="user"))
        for index, (subject, cost) in enumerate((("alice", 1_000), ("boss", 7_000))):
            session.add(
                RequestLog(
                    subject=subject,
                    username=subject,
                    auth_method="oidc",
                    use_case="uc",
                    api="gemini",
                    operation="generateContent",
                    model="mock-1",
                    status=200,
                    outcome="served",
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                    cost_nanos=cost,
                    latency_ms=40 + index,
                    created_at=WHEN,
                )
            )
        await session.commit()


def _fill(client: TestClient, visibility: str) -> None:
    import anyio

    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: _seed(client.app, visibility))


def test_the_table_answers_every_reader_in_every_visibility() -> None:
    """A hole in the table is a sentence nobody wrote; a stale cell is a claim about nothing."""
    want = {(reader, visibility) for reader in READERS for visibility in VISIBILITIES}

    assert set(EXPECTED) == want, (
        f"unanswered: {sorted(want - set(EXPECTED))}; stale: {sorted(set(EXPECTED) - want)}"
    )


@pytest.mark.parametrize(
    ("reader", "visibility"),
    [
        pytest.param(reader, visibility, id=f"{reader}-{visibility}")
        for reader in READERS
        for visibility in VISIBILITIES
    ],
)
def test_whose_figures_the_splits_by_person_carry(reader: str, visibility: str) -> None:
    expected = EXPECTED[(reader, visibility)]
    with _client(READERS[reader]) as client:
        _fill(client, visibility)
        body = client.get(f"/v1beta/reporting{WINDOW}").json()

    for split in ("by_person", "by_member"):
        keys = {row["key"] for row in body[split]}
        if expected is None:
            assert keys == {"alice", "boss"}, f"{split} withheld a figure it should carry"
        else:
            assert keys == expected, f"{split} carried somebody else's figures"

    # And the report **says** which of the two it did, because the console shows one row instead of
    # a table on the strength of it and must not work the rule out a second time (`FRD-206`).
    assert body["people_limited"] is (expected is not None)


@pytest.mark.parametrize("visibility", VISIBILITIES)
def test_the_use_cases_own_totals_are_the_same_in_every_mode(visibility: str) -> None:
    """**The half that must not move.** Narrowing a split by person is a statement about *whose*
    figures a reader sees, never about *how much* the use case spent — and a total that changed
    with the reader would make two screens disagree about one month, which is how a figure stops
    being believed (`FRD-206`).
    """
    seen = []
    for principal in (ALICE, BOSS, GOV):
        with _client(principal) as client:
            _fill(client, visibility)
            body = client.get(f"/v1beta/reporting{WINDOW}").json()
        seen.append((body["totals"]["requests"], body["totals"]["cost_nanos"]))
        # And the breakdowns that are not about a person are whole as well.
        assert {row["key"] for row in body["by_model"]} == {"mock-1"}
        assert {row["key"] for row in body["by_use_case"]} == {"uc"}

    assert seen == [(2, 8_000), (2, 8_000), (2, 8_000)], seen


def test_a_reader_still_sees_their_own_figures_in_the_narrowest_mode() -> None:
    """Otherwise the mode would take away the one thing a person is always entitled to: what they
    themselves used. The use-case overview is built on it (`FRD-626`)."""
    with _client(ALICE) as client:
        _fill(client, "oversight")
        body = client.get(f"/v1beta/reporting{WINDOW}").json()

    (mine,) = body["by_person"]
    assert mine["key"] == "alice"
    assert mine["requests"] == 1
    assert mine["cost_nanos"] == 1_000


def test_the_spreadsheet_carries_what_the_screen_carries() -> None:
    """The export is a renderer over the report that already exists (`FRD-602` §5.3), so it
    inherits this — asserted rather than assumed, because an export is exactly where a narrowing
    gets lost: a second query would have had no idea."""
    with _client(ALICE) as client:
        _fill(client, "own")
        csv = client.get(
            f"/v1beta/reporting{WINDOW}&breakdown=member", headers={"Accept": "text/csv"}
        ).text

    assert "alice" in csv
    assert "boss" not in csv, "the spreadsheet carried a colleague the screen withheld"


def test_one_withholding_use_case_narrows_the_splits_for_the_whole_report() -> None:
    """**All or nothing across the window, by design** (`spend._people_limited_to`). The splits are
    keyed on a person and not on a person *and* a use case, so "everybody except the people in that
    one use case" cannot be expressed in them — and quietly folding the withheld use case's rows
    into an aggregate by person is the disclosure this exists to stop.

    A reader who needs the other use case's people asks for that use case, which is what the
    use-case page does.
    """
    with _client(ALICE) as client:
        import anyio

        async def seed() -> None:
            async with client.app.state.db_sessionmaker() as session:
                session.add(UseCaseRead(slug="open", name="open", payload_visibility="members"))
                session.add(UseCaseRead(slug="uc", name="uc", payload_visibility="own"))
                session.add(UseCaseMemberRead(use_case_slug="open", subject="alice", role="user"))
                for slug, subject in (("open", "carol"), ("uc", "boss"), ("open", "alice")):
                    session.add(
                        RequestLog(
                            subject=subject,
                            username=subject,
                            auth_method="oidc",
                            use_case=slug,
                            api="gemini",
                            operation="generateContent",
                            model="mock-1",
                            status=200,
                            outcome="served",
                            total_tokens=30,
                            cost_nanos=1_000,
                            created_at=WHEN,
                        )
                    )
                await session.commit()

        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(seed)
        reader = Principal(
            subject="alice", method="oidc", roles=("use-case-user",), use_cases=("uc", "open")
        )
        client.app.dependency_overrides[require_principal] = lambda: reader
        body = client.get(f"/v1beta/reporting{WINDOW}").json()

    assert {row["key"] for row in body["by_person"]} == {"alice"}
    # The totals still cover both use cases: what is withheld is who, not how much.
    assert body["totals"]["requests"] == 3
    assert {row["key"] for row in body["by_use_case"]} == {"open", "uc"}


def test_the_read_model_is_asked_once_per_report() -> None:
    """A report builds a dozen queries; the narrowing must not add one per figure. Asserted on the
    **shape** of the decision rather than by counting queries: it is resolved beside the scope, in
    the one place that decides what a report is about."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "aira_gateway"
        / "api"
        / "reporting"
        / "spend.py"
    ).read_text()

    assert source.count("people_restricted_use_cases(") == 1
    assert source.count("_people_limited_to(") == 2  # defined once, called once


async def test_nothing_about_the_rows_changed(tmp_path) -> None:
    """The row restriction and the figure restriction are **different questions** with different
    answers, and the easiest mistake here would be to collapse them: `oversight` deliberately
    leaves the rows (`FRD-622` FR-1c) and deliberately takes the per-person split.

    Asserted together, in one test, because that is the pair a reader of either rule would
    reasonably doubt.
    """
    with _client(ALICE) as client:
        _fill(client, "oversight")
        rows = client.get("/v1beta/traces").json()["traces"]
        figures = client.get(f"/v1beta/reporting{WINDOW}").json()

    assert {row["subject"] for row in rows} == {"alice", "boss"}, "the rows were narrowed"
    assert {row["key"] for row in figures["by_person"]} == {"alice"}


async def test_an_administrator_keeps_the_rows_and_loses_the_split() -> None:
    """The same pair for the use case's administrator, who is the person this mode is really
    about: they still see the traffic they are responsible for, and no longer who spent what."""
    with _client(BOSS) as client:
        _fill(client, "oversight")
        rows = client.get("/v1beta/traces").json()["traces"]
        figures = client.get(f"/v1beta/reporting{WINDOW}").json()

    assert len(rows) == 2
    assert {row["key"] for row in figures["by_person"]} == {"boss"}


async def test_the_use_case_read_model_decides_it_and_nothing_else(tmp_path) -> None:
    """With no row for the use case — an event that has not arrived — nothing is narrowed: a use
    case the gateway has not heard of must not become stricter than one it has, or a half-upgraded
    stack would start withholding figures nobody configured (`FRD-204`)."""
    with _client(ALICE) as client:
        import anyio

        async def only_requests() -> None:
            async with client.app.state.db_sessionmaker() as session:
                for subject in ("alice", "boss"):
                    session.add(
                        RequestLog(
                            subject=subject,
                            username=subject,
                            auth_method="oidc",
                            use_case="uc",
                            api="gemini",
                            operation="generateContent",
                            model="mock-1",
                            status=200,
                            outcome="served",
                            total_tokens=30,
                            cost_nanos=1_000,
                            created_at=WHEN,
                        )
                    )
                await session.commit()

        with anyio.from_thread.start_blocking_portal() as portal:
            portal.call(only_requests)
        body = client.get(f"/v1beta/reporting{WINDOW}").json()

    assert {row["key"] for row in body["by_person"]} == {"alice", "boss"}


async def test_the_narrowing_leaves_the_audit_trail_alone() -> None:
    """A figure withheld is not a figure deleted. The rows the report is built from are untouched,
    which is what lets an oversight role answer the same question later."""
    with _client(ALICE) as client:
        _fill(client, "oversight")
        client.get(f"/v1beta/reporting{WINDOW}")
        async with client.app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert sorted(row.subject for row in rows) == ["alice", "boss"]
