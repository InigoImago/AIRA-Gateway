"""The register of processing activities (`FRD-608`).

Two halves and one rule. The **configuration** half is a reading of fields that already existed —
so the tests that matter about it are the ones where a field means something other than its value:
a retention period beside "prompts are not stored", a released model the catalogue does not have,
a use case somebody retired. The **measured** half is the point of the feature, and its tests are
about the finding it exists to make: traffic that reached a region the configuration does not name.

And the rule underneath both: this endpoint is scoped by `visible_scope`, the same function the
report and the trace list use, so a caller sees exactly the use cases they may see — and an empty
register says which of the two empties it is.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.audit import Outcome
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import (
    ModelRead,
    RequestLog,
    RetentionRun,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)
from aira_gateway.reporting.register import NO_REGION

GOVERNANCE = Principal(subject="gov", method="oidc", roles=("it-steuerung",), username="gov")
MEMBER = Principal(
    subject="ada", method="oidc", roles=("use-case-user",), use_cases=("mine",), username="ada"
)
STRANGER = Principal(subject="bob", method="oidc", roles=("use-case-user",), username="bob")


def _app(principal: Principal = GOVERNANCE):  # noqa: ANN201
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    return app


def _use_case(slug: str, **over: Any) -> UseCaseRead:
    values: dict[str, Any] = {
        "slug": slug,
        "name": slug.title(),
        "description": "Answering customer questions",
        "processing_notes": "Prompt goes to the model, answer comes back.",
        "store_payloads": True,
        "retention_days": 7,
        "restrict_members_to_own_requests": False,
        "tools_enabled": False,
        "prompt_caching_enabled": False,
        "prompt_cache_ttl": "5m",
        "include_reasoning": False,
        "allowed_models": ["gemini-2.5-flash"],
    }
    values.update(over)
    return UseCaseRead(**values)


def _model(name: str, **over: Any) -> ModelRead:
    values: dict[str, Any] = {
        "model": name,
        "provider": "vertex",
        "publisher": "google",
        "approved": True,
        "addressing": {"regions": ["europe-west1"]},
        "capabilities": ["generate"],
    }
    values.update(over)
    return ModelRead(**values)


def _log(use_case: str | None, region: str | None, **over: Any) -> RequestLog:
    values: dict[str, Any] = {
        "subject": "ada",
        "auth_method": "api_key",
        "use_case": use_case,
        "api": "gemini",
        "operation": "generateContent",
        "model": "gemini-2.5-flash",
        "status": 200,
        "outcome": Outcome.SERVED.value,
        "provider": "vertex",
        "region": region,
        "created_at": datetime.now(UTC) - timedelta(hours=1),
    }
    values.update(over)
    return RequestLog(**values)


async def _seed(app, *rows: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _register(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/v1beta/register", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _entry(body: dict[str, Any], slug: str) -> dict[str, Any]:
    found = [row for row in body["use_cases"] if row["slug"] == slug]
    assert found, f"{slug} is not in the register: {[r['slug'] for r in body['use_cases']]}"
    return found[0]


# == the configuration half ======================================================================


async def test_one_row_carries_what_a_register_is_made_of() -> None:
    """The owner's list, in one row: purpose, processing, models, storage, retention, controls.

    Asserted as a whole rather than field by field, because the feature *is* the row — every one of
    these existed before and was one click deeper, one use case at a time.
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine", tools_enabled=True, prompt_caching_enabled=True),
            _model("gemini-2.5-flash"),
            UseCaseMemberRead(use_case_slug="mine", subject="ada", role="admin"),
            UseCaseGroupRead(use_case_slug="mine", group_path="/ai/support", role="user"),
        )
        entry = _entry(_register(client), "mine")

    assert entry["purpose"] == "Answering customer questions"
    assert entry["processing"].startswith("Prompt goes to the model")
    assert entry["prompts_stored"] is True
    assert entry["retention_days"] == 7
    assert entry["members"] == 1
    assert entry["groups"] == 1
    assert entry["status"] == "live"
    assert entry["models"] == [
        {
            "name": "gemini-2.5-flash",
            "provider": "vertex",
            "publisher": "google",
            "regions": ["europe-west1"],
            "approved": True,
            "catalogued": True,
        }
    ]


async def test_a_use_case_that_keeps_nothing_has_no_erasure_deadline() -> None:
    """**Not the configured number beside "not stored".**

    `retention_days` keeps its value in the database when storage is switched off — turning the
    toggle back on should not lose the period somebody chose. Printed in a register, that number
    reads as a promise about data, and there is no data: an erasure deadline for something never
    written is a claim about nothing.
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("quiet", store_payloads=False, retention_days=30))
        entry = _entry(_register(client), "quiet")

    assert entry["prompts_stored"] is False
    assert entry["retention_days"] is None


async def test_a_retired_use_case_is_in_the_register_and_says_so() -> None:
    """`FRD-607` keeps the row as a tombstone, and a register is exactly who needs it: a retired
    use case is still a processing record for as long as its stored prompts exist. Omitting it
    would be a register that quietly stops describing the data it is about."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("gone", deleted_at=datetime.now(UTC)))
        entry = _entry(_register(client), "gone")

    assert entry["status"] == "retired"


async def test_a_model_the_catalogue_does_not_have_is_named_rather_than_dropped() -> None:
    """A use case releasing a model nobody catalogued is a disagreement between the two planes
    (`FRD-608` §4). Dropping it would make the register agree with itself by omission."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine", allowed_models=["ghost-1"]))
        entry = _entry(_register(client), "mine")

    assert entry["models"] == [
        {
            "name": "ghost-1",
            "provider": "",
            "publisher": "",
            "regions": [],
            "approved": False,
            "catalogued": False,
        }
    ]


async def test_a_released_model_nobody_approved_is_visible_as_such() -> None:
    """The other half of the same question, and a different action: not catalogued is a plumbing
    fault, not approved is a decision somebody has to take (`FRD-307`)."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _model("gemini-2.5-flash", approved=False))
        entry = _entry(_register(client), "mine")

    assert entry["models"][0]["catalogued"] is True
    assert entry["models"][0]["approved"] is False


# == the measured half, which is the point =======================================================


async def test_where_the_traffic_actually_went_is_reported_beside_the_configuration() -> None:
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash"),
            _log("mine", "europe-west1"),
            _log("mine", "europe-west1"),
            _log("mine", "europe-west4"),
        )
        entry = _entry(_register(client), "mine")

    assert entry["requests"] == 3
    assert entry["processed_in"] == [
        {"region": "europe-west1", "provider": "vertex", "requests": 2},
        {"region": "europe-west4", "provider": "vertex", "requests": 1},
    ]


async def test_a_region_the_configuration_does_not_name_is_the_finding() -> None:
    """**The reason this feature exists.**

    `FRD-611` made an impermissible region unconfigurable, which closes the *configuration* door
    and says nothing about the *measurement*. A model catalogued in a permitted region and served
    from another is still a finding, and it is the one a governance role exists to make.
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash", addressing={"regions": ["europe-west1"]}),
            _log("mine", "europe-west1"),
            _log("mine", "us-central1"),
        )
        entry = _entry(_register(client), "mine")

    assert entry["unexpected_regions"] == ["us-central1"]


async def test_a_request_with_no_region_is_not_a_transfer() -> None:
    """**Unknown is not a violation.** Most dialects address a model by name alone, and the mock
    and local providers run in the container. Counting those as a transfer would make the finding
    column noise, which is the reliable way to have a finding ignored.

    Reported under its provider all the same — a reader has to be able to tell "processed somewhere
    this column cannot express" from "nobody asked".
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash"),
            _log("mine", None, provider="mock"),
        )
        entry = _entry(_register(client), "mine")

    assert entry["unexpected_regions"] == []
    assert entry["processed_in"] == [
        {"region": NO_REGION, "provider": "mock", "requests": 1},
    ]


async def test_a_use_case_whose_models_name_no_region_reports_no_finding() -> None:
    """Nothing to disagree with. A use case on an OpenAI-compatible server has no configured region
    at all, and calling every region it used unexpected would be a column that is always red."""
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash", addressing=None),
            _log("mine", "somewhere"),
        )
        entry = _entry(_register(client), "mine")

    assert entry["unexpected_regions"] == []


async def test_the_installation_summary_counts_traffic_that_names_no_use_case() -> None:
    """Break-glass keys, the console's own model checks, demo traffic — the 59 rows `FRD-610` was
    written about. A summary that only added up its own rows would omit exactly the traffic nobody
    owns, which is the traffic a register is most likely to be asked about."""
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash"),
            _log("mine", "europe-west1"),
            _log(None, "us-central1"),
        )
        body = _register(client)

    assert body["processed_in"] == [
        {"region": "europe-west1", "provider": "vertex", "requests": 1},
        {"region": "us-central1", "provider": "vertex", "requests": 1},
    ]


# == who may read it =============================================================================


async def test_a_member_sees_their_own_use_case_and_not_the_others() -> None:
    app = _app(MEMBER)
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _use_case("theirs"), _model("gemini-2.5-flash"))
        body = _register(client)

    assert [row["slug"] for row in body["use_cases"]] == ["mine"]
    assert body["scope"] == "use_cases"


async def test_a_member_is_not_shown_the_installations_own_traffic() -> None:
    """The summary is for a reader who sees every use case. Unattributed traffic is not a member's
    to see, and folding it into their summary would be the widening `visible_scope` prevents."""
    app = _app(MEMBER)
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _log(None, "us-central1"))
        body = _register(client)

    assert body["processed_in"] == []


async def test_a_caller_who_is_a_member_of_nothing_gets_an_empty_register_not_a_refusal() -> None:
    """ "There is nothing here" and "you may not look" are different facts, and `scope` says which
    one this is — the same answer the report and the findings list give."""
    app = _app(STRANGER)
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"))
        body = _register(client)

    assert body["use_cases"] == []
    assert body["scope"] == "use_cases"


async def test_governance_sees_every_use_case() -> None:
    app = _app(GOVERNANCE)
    with TestClient(app) as client:
        await _seed(app, _use_case("one"), _use_case("two"))
        body = _register(client)

    assert [row["slug"] for row in body["use_cases"]] == ["one", "two"]
    assert body["scope"] == "all"


def test_the_register_needs_a_credential() -> None:
    """It names every use case, its purpose and who is in it. It is not public."""
    with TestClient(create_app(GatewaySettings(auth_required=True))) as client:
        assert client.get("/v1beta/register").status_code == 401


# == the CSV, which is the deliverable ===========================================================


def _rows(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.lstrip("﻿"))))


async def test_the_csv_is_the_same_rows_and_says_which_period_it_covers() -> None:
    """A register printed without the period its measured half covers is a document whose second
    half cannot be checked against anything."""
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app, _use_case("mine"), _model("gemini-2.5-flash"), _log("mine", "europe-west1")
        )
        response = client.get("/v1beta/register", headers={"Accept": "text/csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "aira-register_" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("﻿"), "Excel needs the BOM to read this as UTF-8"
    assert "\r\n" in body, "RFC 4180 says CRLF"

    rows = _rows(body)
    assert rows[0][0].startswith("# AIRA register of processing activities")
    header = next(row for row in rows if row and row[0] == "use_case")
    assert "purpose" in header and "retention_days" in header
    assert "regions_outside_the_configuration" in header
    data = rows[rows.index(header) + 1]
    assert data[0] == "mine"
    assert "Answering customer questions" in data[3]


async def test_a_purpose_a_spreadsheet_would_execute_is_written_as_text() -> None:
    """Half these columns are prose somebody typed into a form, and the file's whole point is that
    it opens in Excel. `aira_common.spreadsheet` owns the rule; this is the third file to use it."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine", description="=HYPERLINK(1)"))
        body = client.get("/v1beta/register", headers={"Accept": "text/csv"}).text

    purpose = next(row[3] for row in _rows(body) if row and row[0] == "mine")
    assert purpose == "'=HYPERLINK(1)", purpose
    assert not purpose.startswith(("=", "+", "-", "@"))


async def test_the_csv_shows_the_same_use_cases_as_the_json_for_the_same_caller() -> None:
    """`FRD-602` §1's rule: an export that returns more than the screen it was exported from is
    the failure this shape exists to prevent, and it is prevented by the CSV being a *rendering*
    of the very same result rather than a second query."""
    app = _app(MEMBER)
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _use_case("theirs"))
        seen = {row["slug"] for row in _register(client)["use_cases"]}
        body = client.get("/v1beta/register", headers={"Accept": "text/csv"}).text

    assert seen == {"mine"}
    assert "theirs" not in body


@pytest.mark.parametrize("accept", ["application/xml", "text/html"])
async def test_a_format_this_endpoint_does_not_serve_is_a_406(accept: str) -> None:
    app = _app()
    with TestClient(app) as client:
        assert client.get("/v1beta/register", headers={"Accept": accept}).status_code == 406


async def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.get("/v1beta/register", params={"from": "2026-08-02", "to": "2026-08-01"})
    assert response.status_code == 400
    assert "after" in response.json()["error"]["message"]


async def test_the_measured_half_respects_the_window() -> None:
    """The configuration half is timeless and the measured half is not — a register for August must
    not carry September's traffic, or the two halves describe different things."""
    app = _app()
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            _model("gemini-2.5-flash"),
            _log("mine", "europe-west1", created_at=datetime(2020, 1, 1, tzinfo=UTC)),
        )
        entry = _entry(_register(client), "mine")

    assert entry["requests"] == 0
    assert entry["processed_in"] == []


# == erasure as evidence, not as a setting =======================================================


async def test_the_register_says_when_the_sweep_last_ran_and_what_it_took() -> None:
    """**The difference between a policy document and a record.**

    Every row states an erasure deadline; this states that the sweep enforcing them ran. Those two
    figures have been returned by `RetentionService.prune` since `FRD-404` and were read by
    nothing — they went into a log line and out of reach.
    """
    app = _app()
    ran = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    with TestClient(app) as client:
        await _seed(
            app,
            _use_case("mine"),
            RetentionRun(ran_at=ran - timedelta(days=1), payloads_cleared=7, rows_deleted=0),
            RetentionRun(ran_at=ran, payloads_cleared=1412, rows_deleted=3),
        )
        body = _register(client)

    assert body["last_erasure"] == {
        "ran_at": ran.isoformat(),
        "payloads_cleared": 1412,
        "rows_deleted": 3,
    }


async def test_a_sweep_that_has_never_run_is_null_and_not_zero() -> None:
    """*Unknown is not zero*, in the column where it matters most. `0 cleared` reads as "the sweep
    ran and there was nothing to remove"; `null` reads as what it is, which is that the erasure
    this document promises has no record of having happened."""
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"))
        body = _register(client)

    assert body["last_erasure"] is None


async def test_the_csv_prints_the_evidence_or_says_there_is_none() -> None:
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"))
        without = client.get("/v1beta/register", headers={"Accept": "text/csv"}).text
        await _seed(
            app,
            RetentionRun(
                ran_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
                payloads_cleared=1412,
                rows_deleted=0,
            ),
        )
        with_evidence = client.get("/v1beta/register", headers={"Accept": "text/csv"}).text

    assert "no recorded pass" in without
    assert "the last retention pass" in with_evidence
    assert "1412" in with_evidence


async def test_a_pass_leaves_a_row_that_says_what_it_removed() -> None:
    """The write itself, at the other end: the sweep records what it did, inside the same
    transaction as the deletions it describes. A record of an erasure that could commit while the
    erasure rolled back would be worse than no record, because somebody would believe it."""
    from sqlalchemy import select

    from aira_gateway.retention import RetentionService

    app = _app()
    with TestClient(app) as client:  # noqa: F841 - the app builds the schema
        sessions = app.state.db_sessionmaker
        old = datetime.now(UTC) - timedelta(days=30)
        await _seed(
            app,
            _use_case("mine", retention_days=1),
            _log("mine", "europe-west1", created_at=old, request_payload={"contents": "hi"}),
        )

        result = await RetentionService(sessions).prune()

        async with sessions() as session:
            runs = list((await session.execute(select(RetentionRun))).scalars().all())

    assert result.payloads_cleared == 1
    assert [(run.payloads_cleared, run.rows_deleted) for run in runs] == [(1, 0)]


# == is what we think is configured what is actually running (§4) ================================


async def test_the_register_names_every_model_the_gateway_holds() -> None:
    """**The comparison `FRD-608` §4 says this screen exists for.**

    Both planes keep a catalogue, one feeds the other over Kafka, and nothing compared them: a
    model the gateway could serve sat in its read-model with no row in Management, so no console
    screen showed it and no role could remove it. The gateway can only report its own half; the
    console holds the other and does the diff.
    """
    app = _app()
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _model("gemini-2.5-flash"), _model("mock-1"))
        body = _register(client)

    assert body["catalogue"] == ["gemini-2.5-flash", "mock-1"]


async def test_the_catalogue_is_not_shown_to_somebody_who_oversees_nothing() -> None:
    """An installation-wide list is not a member's to see — the same line `processed_in` draws,
    for the same reason."""
    app = _app(MEMBER)
    with TestClient(app) as client:
        await _seed(app, _use_case("mine"), _model("gemini-2.5-flash"))
        body = _register(client)

    assert body["catalogue"] == []
