"""Strict data mode: nobody but the incident roles learns what a named colleague did (`FRD-627`).

The per-use-case ladder (`members`, `own`, `oversight`) left the choice with each use case's
administrator, and its top rung still listed every colleague's requests by name and key, every
finding that named them, and — to `report.read_all` — every split by person. Under the mode, on by
default, a person is seen by Global Administrator and IT Security (`incident.investigate`) and by
the person themselves; everybody else keeps the rows, the totals and every breakdown not keyed on
a human.

Every use case below **stores `members`**, the most open setting there is: the mode decides on
read, so the stored value must not matter.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient

from aira_common.permissions import Permission, builtin_roles, parse_permissions
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import (
    AccessSuspension,
    AnomalyEvent,
    RequestLog,
    RoleRead,
    UseCaseMemberRead,
    UseCaseRead,
)

WHEN = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
WINDOW = "?from=2026-10-01&to=2026-11-01"

#: A member, an administrator of the use case, IT Steuerung (every figure, no person) and IT
#: Security (an incident role, who sees people).
ALICE = Principal(subject="alice", method="oidc", roles=("use-case-user",), use_cases=("uc",))
BOSS = Principal(subject="boss", method="oidc", roles=("use-case-user",), use_cases=("uc",))
GOV = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
SEC = Principal(subject="sec", method="oidc", roles=("it-security",))


@pytest.fixture(autouse=True)
def _the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mode as shipped: nothing in the environment says anything about it."""
    monkeypatch.delenv("AIRA_STRICT_DATA_MODE", raising=False)


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


async def _seed(app) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(
            UseCaseRead(slug="uc", name="uc", payload_visibility="members", store_payloads=True)
        )
        session.add(UseCaseMemberRead(use_case_slug="uc", subject="boss", role="admin"))
        session.add(UseCaseMemberRead(use_case_slug="uc", subject="alice", role="user"))
        for index, (subject, credential) in enumerate((("alice", None), ("boss", "bosskey"))):
            session.add(
                RequestLog(
                    id=f"row-{subject}",
                    subject=subject,
                    username=subject,
                    auth_method="api_key" if credential else "oidc",
                    credential=credential,
                    use_case="uc",
                    api="gemini",
                    operation="generateContent",
                    model="mock-1",
                    status=200,
                    outcome="served",
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                    cost_nanos=1_000 * (index + 1),
                    latency_ms=40 + index,
                    request_payload={"contents": "secret"},
                    response_payload={"text": "answer"},
                    created_at=WHEN,
                )
            )
        common = {
            "use_case": "uc",
            "observed": 90,
            "threshold": 50,
            "sample": 10,
            "window_minutes": 15,
            "action_taken": "alert",
            "detail": "90% refusals",
            "rule_id": 1,
            "rule_name": "rule",
            "kind": "refusal_rate",
        }
        session.add(AnomalyEvent(target="subject", target_value="boss", **common))
        session.add(AnomalyEvent(target="subject", target_value="alice", **common))
        session.add(AnomalyEvent(target="use_case", target_value="uc", **common))
        session.add(
            AccessSuspension(
                use_case="uc",
                target="subject",
                target_value="boss",
                action="block",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                author="user:sec",
                reason="under investigation",
            )
        )
        await session.commit()


def _seeded(principal: Principal) -> TestClient:
    client = _client(principal)
    client.__enter__()
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: _seed(client.app))
    return client


def test_the_mode_is_on_unless_an_operator_switches_it_off() -> None:
    assert GatewaySettings().strict_data_mode is True


# ---- figures per person (FR-4, FR-5) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("reader", "keys"),
    [
        pytest.param(ALICE, {"alice"}, id="a-member-sees-themselves"),
        pytest.param(BOSS, {"boss"}, id="an-administrator-sees-themselves"),
        pytest.param(GOV, set(), id="reading-every-figure-is-no-exemption"),
        pytest.param(SEC, {"alice", "boss"}, id="an-incident-role-sees-people"),
    ],
)
def test_a_split_by_person_carries_only_what_the_reader_may_see(
    reader: Principal, keys: set[str]
) -> None:
    client = _seeded(reader)
    body = client.get(f"/v1beta/reporting{WINDOW}").json()

    for split in ("by_person", "by_member"):
        assert {row["key"] for row in body[split]} == keys, split
    assert body["strict_data"] is True
    assert body["people_limited"] is (reader is not SEC)


@pytest.mark.parametrize("reader", [ALICE, BOSS, GOV, SEC], ids=["member", "admin", "gov", "sec"])
def test_totals_and_breakdowns_not_keyed_on_a_person_stay(reader: Principal) -> None:
    body = _seeded(reader).get(f"/v1beta/reporting{WINDOW}&use_case=uc").json()

    assert body["totals"]["requests"] == 2
    assert {row["key"] for row in body["by_model"]} == {"mock-1"}


@pytest.mark.parametrize("reader", [ALICE, SEC], ids=["member", "even-an-incident-role"])
def test_no_split_by_person_carries_a_latency(reader: Principal) -> None:
    body = _seeded(reader).get(f"/v1beta/reporting{WINDOW}").json()

    rows = body["by_person"] + body["by_member"]
    assert rows
    for row in rows:
        assert "avg_latency_ms" not in row
        assert "max_latency_ms" not in row
    # A breakdown not keyed on a human keeps it: how fast a model answers is no person's.
    assert "avg_latency_ms" in body["by_model"][0]


def test_the_member_csv_has_no_latency_columns() -> None:
    response = _seeded(SEC).get(
        f"/v1beta/reporting{WINDOW}&breakdown=member", headers={"accept": "text/csv"}
    )

    header = next(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert "avg_latency_ms" not in header
    assert "max_latency_ms" not in header


@pytest.mark.parametrize("reader", [ALICE, GOV], ids=["member", "gov"])
def test_naming_a_colleague_answers_an_empty_report(reader: Principal) -> None:
    body = _seeded(reader).get(f"/v1beta/reporting{WINDOW}&person=boss").json()

    assert body["totals"]["requests"] == 0
    assert body["in_scope"] is False


@pytest.mark.parametrize("reader", [GOV, SEC], ids=["gov", "even-an-incident-role"])
def test_ki_usage_reports_each_use_case_and_no_person(reader: Principal) -> None:
    url = "/kira/api/external/ki-usage?startDatum=2026-10-01&endDatum=2026-11-01"

    rows = _seeded(reader).get(url).json()

    assert [(row["user_id"], row["entry_count"]) for row in rows] == [("uc", 2)]
    assert rows[0]["token_input_sum"] == 20


# ---- request rows (FR-7) -----------------------------------------------------------------------


def test_another_persons_row_is_listed_without_its_person() -> None:
    rows = {row["id"]: row for row in _seeded(ALICE).get("/v1beta/traces").json()["traces"]}

    assert set(rows) == {"row-alice", "row-boss"}, "every row stays listed"
    theirs = rows["row-boss"]
    assert theirs["subject"] is None
    assert theirs["credential"] is None
    assert theirs["person_withheld"] is True
    assert theirs["cost_nanos"] == 2_000, "what it cost is the use case's"
    mine = rows["row-alice"]
    assert mine["subject"] == "alice"
    assert mine["person_withheld"] is False


def test_an_administrator_of_the_use_case_is_no_exception() -> None:
    rows = {row["id"]: row for row in _seeded(BOSS).get("/v1beta/traces").json()["traces"]}

    assert rows["row-alice"]["subject"] is None
    assert rows["row-boss"]["credential"] == "bosskey"


def test_an_incident_role_sees_every_person() -> None:
    rows = {row["id"]: row for row in _seeded(SEC).get("/v1beta/traces").json()["traces"]}

    assert rows["row-boss"]["subject"] == "boss"
    assert rows["row-boss"]["credential"] == "bosskey"
    assert rows["row-boss"]["person_withheld"] is False


@pytest.mark.parametrize("query", ["subject=boss", "credential=bosskey"])
def test_filtering_by_person_or_key_is_refused(query: str) -> None:
    client = _seeded(ALICE)

    assert client.get(f"/v1beta/traces?{query}").status_code == 403
    assert _seeded(SEC).get(f"/v1beta/traces?{query}").status_code == 200


# ---- content (FR-3) ----------------------------------------------------------------------------


def test_a_use_case_storing_members_reads_as_oversight() -> None:
    response = _seeded(BOSS).get("/v1beta/traces/row-alice/payload")

    assert response.status_code == 403
    assert "oversight" in response.text.lower()
    assert _seeded(SEC).get("/v1beta/traces/row-alice/payload").json()["available"] is True


def test_the_register_states_the_visibility_that_decides() -> None:
    body = _seeded(SEC).get(f"/v1beta/register{WINDOW}").json()

    assert body["use_cases"][0]["content_visibility"] == "oversight"


# ---- findings and stops (FR-8) -----------------------------------------------------------------


def test_a_finding_names_nobody_but_the_person_it_is_about() -> None:
    events = _seeded(ALICE).get("/v1beta/anomalies").json()["events"]
    by_target = {(e["target"], e["target_value"]): e for e in events}

    assert ("subject", "alice") in by_target, "a person is told what was found about them"
    assert ("subject", "boss") not in by_target
    withheld = [e for e in events if e["target_withheld"]]
    assert len(withheld) == 1 and withheld[0]["target_value"] is None
    assert by_target[("use_case", "uc")]["target_withheld"] is False


def test_an_incident_role_sees_whom_a_finding_names() -> None:
    events = _seeded(SEC).get("/v1beta/anomalies").json()["events"]

    assert {e["target_value"] for e in events} == {"alice", "boss", "uc"}


def test_a_stop_on_a_person_names_them_to_the_incident_roles_only() -> None:
    governed = _seeded(GOV).get("/v1beta/suspensions").json()["suspensions"]
    secured = _seeded(SEC).get("/v1beta/suspensions").json()["suspensions"]

    assert governed[0]["target_value"] is None
    assert governed[0]["target_withheld"] is True
    assert secured[0]["target_value"] == "boss"


# ---- the mode switched off restores the use case's own setting ---------------------------------


def test_switched_off_the_stored_setting_decides_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRA_STRICT_DATA_MODE", "false")
    rows = {row["id"]: row for row in _seeded(ALICE).get("/v1beta/traces").json()["traces"]}

    assert rows["row-boss"]["subject"] == "boss"
    assert _seeded(BOSS).get("/v1beta/traces/row-alice/payload").json()["available"] is True


# ---- who may hold the permissions that see a person (FR-2) -------------------------------------


@pytest.mark.parametrize(
    "permission", [Permission.PAYLOAD_READ_ANY, Permission.INCIDENT_INVESTIGATE]
)
def test_no_stored_role_holds_what_sees_a_person(permission: Permission) -> None:
    stored = [str(permission), str(Permission.ROLE_READ)]

    assert permission not in parse_permissions(stored, strict_data=True)
    assert permission in parse_permissions(stored, strict_data=False)
    steuerung = next(
        role
        for role in builtin_roles({}, frozenset({permission}), strict_data=True)
        if role.slug == "it-steuerung"
    )
    assert permission not in steuerung.permissions
    security = next(
        role for role in builtin_roles({}, strict_data=True) if role.slug == "it-security"
    )
    assert permission in security.permissions


def test_reading_an_unknown_row_is_still_not_found() -> None:
    """The withholding happens after the scope, never instead of it."""
    client = _seeded(ALICE)

    assert client.get("/v1beta/traces/no-such-row/payload").status_code == 404


async def _a_custom_role_reading_content(app) -> frozenset[Permission]:
    async with app.state.db_sessionmaker() as session:
        session.add(
            RoleRead(
                slug="auditors",
                label="Auditors",
                group_path="/aira/auditors",
                permissions=[str(Permission.PAYLOAD_READ_ANY), str(Permission.ROLE_READ)],
                builtin=False,
            )
        )
        await session.commit()
    definitions = await app.state.role_definitions.definitions()
    return next(role for role in definitions if role.slug == "auditors").permissions


def test_the_gateway_resolves_stored_roles_under_the_mode() -> None:
    """What the app wires, not only what the function does: a custom role granted content while the
    mode was off confers it nowhere while it is on."""
    with _client(SEC) as client, anyio.from_thread.start_blocking_portal() as portal:
        held = portal.call(lambda: _a_custom_role_reading_content(client.app))

    assert Permission.ROLE_READ in held
    assert Permission.PAYLOAD_READ_ANY not in held


# ---- telemetry and logs carry a pseudonym (FR-14) ----------------------------------------------


def test_a_request_span_names_its_caller_by_pseudonym(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spans and the SIEM feed leave AIRA's role checks behind, so under the mode the caller there
    is a pseudonym — what the app configures, read off the attribution the span receives."""
    from types import SimpleNamespace

    from aira_common.pseudonym import pseudonym
    from aira_gateway.auth import attribution as module

    written: dict[str, object] = {}
    monkeypatch.setattr(module, "set_span_attributes", written.update)
    monkeypatch.setattr(module, "attribute_model_calls_to", lambda _identity: None)
    with _client(ALICE):  # builds the app, which configures the process for the mode
        module.attribute(
            SimpleNamespace(state=SimpleNamespace()),
            module.Attribution(subject="alice", method="oidc", use_case="uc"),
        )
        attributed = written.pop("aira.subject")
        module.mark_refused("forbidden", 403, subject="alice")
        refused = written.pop("aira.subject")

    # Each on its own: a request that was served, and one refused before attribution.
    assert attributed == refused == pseudonym("alice") != "alice"
    assert str(attributed).startswith("p-")
