"""Spend and usage reporting (FRD-601).

The arithmetic matters, but the visibility rule matters more: a wrong sum is a wrong figure, a
wrong scope is one use case's spend shown to another. So the scope tests come first and cover all
three cases — oversight, membership, and neither — because a test that only shows somebody seeing
something would pass against an endpoint that shows everything to everyone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.money import to_nanos
from aira_gateway.api.reporting import visible_scope
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog
from aira_gateway.reporting.service import ReportingService

AUGUST = datetime(2026, 8, 1, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _log(
    sessionmaker,
    *,
    use_case: str | None = "uc",
    subject: str = "alice",
    model: str = "mock-1",
    when: datetime = AUGUST + timedelta(days=1),
    prompt: int = 10,
    completion: int = 20,
    cost: int | None = 1000,
    status: int = 200,
    latency: int | None = 40,
    outcome: str | None = None,
) -> None:
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                subject=subject,
                auth_method="api_key",
                use_case=use_case,
                api="gemini",
                operation="generateContent",
                model=model,
                status=status,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cost_nanos=cost,
                latency_ms=latency,
                outcome=outcome,
                created_at=when,
            )
        )
        await session.commit()


# ---- who may see what --------------------------------------------------------------------


def test_oversight_is_scoped_to_everything() -> None:
    assert visible_scope(Principal(subject="s", method="oidc", roles=("it-steuerung",))) is None


def test_a_member_is_scoped_to_their_own_use_cases() -> None:
    principal = Principal(subject="s", method="oidc", use_cases=("a", "b"))
    assert visible_scope(principal) == ("a", "b")


def test_neither_oversight_nor_membership_is_scoped_to_nothing() -> None:
    """The empty tuple and None must never be confused: one is "no use case", the other is
    "every use case", and swapping them shows an installation's whole spend to somebody entitled
    to one corner of it."""
    scope = visible_scope(Principal(subject="s", method="oidc"))
    assert scope == ()
    assert scope is not None


def test_an_api_key_is_scoped_to_the_use_case_it_was_issued_for() -> None:
    principal = Principal(subject="s", method="api_key", use_cases=("bound",))
    assert visible_scope(principal) == ("bound",)


async def test_a_caller_with_no_memberships_gets_an_empty_report_not_an_error(
    sessionmaker,
) -> None:
    """Having nothing to show is not a failure. Refusing would say "you may not look" when the
    truth is "there is nothing there yet"."""
    await _log(sessionmaker, use_case="somebody-elses")
    report = await ReportingService(sessionmaker).report((), AUGUST, SEPTEMBER)

    assert report["totals"]["requests"] == 0
    assert report["by_use_case"] == []


async def test_a_member_sees_their_own_use_case_and_no_other(sessionmaker) -> None:
    await _log(sessionmaker, use_case="mine", cost=to_nanos("1.00"))
    await _log(sessionmaker, use_case="theirs", cost=to_nanos("99.00"))

    report = await ReportingService(sessionmaker).report(("mine",), AUGUST, SEPTEMBER)

    assert [row["key"] for row in report["by_use_case"]] == ["mine"]
    assert report["totals"]["requests"] == 1
    assert report["totals"]["cost"] == "1.00"


async def test_oversight_sees_every_use_case(sessionmaker) -> None:
    await _log(sessionmaker, use_case="mine", cost=to_nanos("1.00"))
    await _log(sessionmaker, use_case="theirs", cost=to_nanos("2.00"))

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert {row["key"] for row in report["by_use_case"]} == {"mine", "theirs"}
    assert report["totals"]["cost"] == "3.00"


# ---- the figures -------------------------------------------------------------------------


async def test_tokens_are_reported_by_direction_as_well_as_in_total(sessionmaker) -> None:
    """The split is what makes a spend figure explicable: output is billed several times higher
    than input, so a total alone cannot be reconciled against an invoice."""
    await _log(sessionmaker, prompt=10, completion=20)
    await _log(sessionmaker, prompt=5, completion=1)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["prompt_tokens"] == 15
    assert totals["completion_tokens"] == 21
    assert totals["total_tokens"] == 36


async def test_unpriced_traffic_is_counted_apart_and_not_summed_as_free(sessionmaker) -> None:
    """The rule from FRD-403 §4.4, in the place where a total is most likely to be believed."""
    await _log(sessionmaker, cost=to_nanos("2.00"))
    await _log(sessionmaker, cost=None)
    await _log(sessionmaker, cost=None)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["cost"] == "2.00"
    assert totals["unpriced_requests"] == 2
    assert totals["requests"] == 3  # the traffic is not hidden, only its cost is unknown


async def test_failed_requests_are_reported_rather_than_filtered_out(sessionmaker) -> None:
    """A refused request still consumed a rate limit and possibly an upstream call. Dropping it
    would make a period of outages look quiet."""
    await _log(sessionmaker, status=200)
    await _log(sessionmaker, status=429, cost=None)
    await _log(sessionmaker, status=502, cost=None)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["requests"] == 3
    assert totals["failed_requests"] == 2


async def test_latency_is_reported_as_an_average_and_a_maximum(sessionmaker) -> None:
    """Neither is a percentile, and the report does not pretend otherwise (FRD-601 §4.2).
    Together they at least show whether the spread is wide."""
    await _log(sessionmaker, latency=10)
    await _log(sessionmaker, latency=30)
    await _log(sessionmaker, latency=200)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["avg_latency_ms"] == 80
    assert totals["max_latency_ms"] == 200


async def test_money_crosses_as_an_exact_string_and_as_an_integer(sessionmaker) -> None:
    """The pair FRD-403 established: a human reads the string, a bar divides the integer, and
    neither is a float."""
    await _log(sessionmaker, cost=to_nanos("0.125"))

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["cost_nanos"] == 125_000_000
    assert isinstance(totals["cost_nanos"], int)
    assert totals["cost"] == "0.13"  # rounded for display only


# ---- the window --------------------------------------------------------------------------


async def test_the_window_is_half_open(sessionmaker) -> None:
    """A request at midnight belongs to exactly one month. Both bounds inclusive would count it
    twice across two reports and make the year add up to more than it was."""
    await _log(sessionmaker, when=AUGUST)  # first instant: included
    await _log(sessionmaker, when=SEPTEMBER)  # first instant of the next window: excluded
    await _log(sessionmaker, when=AUGUST - timedelta(microseconds=1))  # just before: excluded

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["requests"] == 1


async def test_an_empty_period_reports_zeroes_rather_than_nothing(sessionmaker) -> None:
    """A screen that has to distinguish "no traffic" from "the field is missing" will get it
    wrong; the shape stays the same either way."""
    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert report["totals"]["requests"] == 0
    assert report["totals"]["cost"] == "0.00"
    assert report["totals"]["avg_latency_ms"] is None  # unknown, not zero
    assert report["by_model"] == []


# ---- the breakdowns ----------------------------------------------------------------------


async def test_the_breakdowns_group_by_use_case_model_and_member(sessionmaker) -> None:
    await _log(sessionmaker, use_case="a", model="m1", subject="alice", cost=to_nanos("1.00"))
    await _log(sessionmaker, use_case="a", model="m2", subject="bob", cost=to_nanos("2.00"))
    await _log(sessionmaker, use_case="b", model="m1", subject="alice", cost=to_nanos("4.00"))

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert {r["key"]: r["cost"] for r in report["by_use_case"]} == {"a": "3.00", "b": "4.00"}
    assert {r["key"]: r["cost"] for r in report["by_model"]} == {"m1": "5.00", "m2": "2.00"}
    assert {r["key"]: r["cost"] for r in report["by_member"]} == {"alice": "5.00", "bob": "2.00"}


async def test_the_costliest_group_comes_first(sessionmaker) -> None:
    """The question the report is opened with is "what is this costing", so the answer is the
    first row rather than something to be found by sorting."""
    await _log(sessionmaker, use_case="small", cost=to_nanos("1.00"))
    await _log(sessionmaker, use_case="large", cost=to_nanos("50.00"))

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert [row["key"] for row in report["by_use_case"]] == ["large", "small"]


async def test_traffic_with_no_use_case_is_named_rather_than_dropped(sessionmaker) -> None:
    """Unattributed traffic — a break-glass key, demo mode — is still spend somebody paid for."""
    await _log(sessionmaker, use_case=None, cost=to_nanos("3.00"))

    report = await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER)

    assert [row["key"] for row in report["by_use_case"]] == ["(none)"]


# ---- the endpoint ------------------------------------------------------------------------


def _client(**settings) -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, **settings)))


def test_the_endpoint_defaults_to_the_current_month() -> None:
    with _client() as client:
        body = client.get("/v1beta/reporting").json()

    assert body["from"].endswith("-01T00:00:00+00:00")
    assert body["totals"]["requests"] == 0


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with _client() as client:
        response = client.get("/v1beta/reporting?from=2026-09-01&to=2026-08-01")

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"


def test_an_unbounded_window_is_refused() -> None:
    """A mistyped year must produce an error, not a report over all of history."""
    with _client() as client:
        response = client.get("/v1beta/reporting?from=2000-01-01&to=2026-01-01")

    assert response.status_code == 400
    assert "at most" in response.json()["error"]["message"]


def test_a_malformed_timestamp_says_which_field_is_wrong() -> None:
    with _client() as client:
        response = client.get("/v1beta/reporting?from=yesterday")

    assert response.status_code == 400
    assert "'from'" in response.json()["error"]["message"]


def test_the_endpoint_requires_a_credential() -> None:
    """Spend across an installation is not public, even though it carries no payloads."""
    with TestClient(create_app(GatewaySettings(auth_required=True))) as client:
        assert client.get("/v1beta/reporting").status_code == 401


def test_the_report_says_which_scope_it_was_built_with() -> None:
    """So a screen can tell "everything" from "your use cases" without guessing from the rows."""
    with _client() as client:
        assert client.get("/v1beta/reporting").json()["scope"] == "all"


async def test_a_refused_request_is_not_counted_as_unpriced(sessionmaker) -> None:
    """Found live: the console reported **105** unpriced requests where **5** had run unpriced.

    A refused row has a NULL cost for the opposite reason to an unpriced one — nothing was spent
    because nothing ran. Counting both made the "spend is a lower bound" caveat permanent, and a
    warning that is always present is one nobody reads. The project's own rule in the direction it
    was missing: unknown is not zero, and **zero is not unknown**.
    """
    await _log(sessionmaker, cost=None, outcome="served")
    await _log(sessionmaker, cost=None, outcome="rate_limited", status=429)
    await _log(sessionmaker, cost=None, outcome="budget_exceeded", status=429)
    await _log(sessionmaker, cost=None, outcome="invalid_request", status=400)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["requests"] == 4
    assert totals["unpriced_requests"] == 1


async def test_a_row_written_before_outcomes_existed_still_counts_as_unpriced(
    sessionmaker,
) -> None:
    """A NULL outcome predates `FRD-122`, when only served requests were logged at all — so it was
    one. Excluding it would quietly change a historical figure while fixing a present one."""
    await _log(sessionmaker, cost=None, outcome=None)

    totals = (await ReportingService(sessionmaker).report(None, AUGUST, SEPTEMBER))["totals"]

    assert totals["unpriced_requests"] == 1


# ---- one use case at a time (FRD-603) ----------------------------------------------------


def _filtered_client(principal: Principal | None = None) -> TestClient:
    """A client whose caller is whoever the test says.

    Driven through the **route** rather than against `ReportingService`, because the property
    these tests are about lives in the route: the service has always been able to report one use
    case, and what was missing was a caller able to ask for one without being able to ask for
    somebody else's.
    """
    app = create_app(GatewaySettings(auth_required=False))
    if principal is not None:
        app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def _fill(client: TestClient, *rows: RequestLog) -> None:
    async def seed(sessions) -> None:
        async with sessions() as session:
            for row in rows:
                session.add(row)
            await session.commit()

    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(seed, client.app.state.db_sessionmaker)


def _row(**over) -> RequestLog:
    values = {
        "subject": "alice",
        "auth_method": "oidc",
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
        "created_at": datetime.now(UTC),
    }
    values.update(over)
    return RequestLog(**values)


def test_a_use_case_with_no_budget_still_reports_what_it_consumed() -> None:
    """The defect this endpoint parameter exists for, stated as the question that found it.

    Consumption was only ever *displayed* as a fraction of a limit, and `BudgetService.usage`
    iterates the use case's **budget rows** — so a use case with no budget answered `[]` and the
    console showed nothing at all. Measured on the running stack: the smoke-test use case had 59
    requests, 10,664 tokens and a real cost in `request_logs`, and not one of those figures was
    reachable from its own page.

    Nothing was ever uncalculated. The figure had no reader.
    """
    with _filtered_client() as client:
        _fill(client, _row(use_case="smoke-test", total_tokens=180, cost_nanos=62_000))

        body = client.get("/v1beta/reporting?use_case=smoke-test").json()

    assert body["totals"]["requests"] == 1
    assert body["totals"]["total_tokens"] == 180
    assert body["totals"]["cost_nanos"] == 62_000
    assert body["in_scope"] is True


def test_a_use_case_filter_narrows_the_report_to_that_use_case() -> None:
    with _filtered_client() as client:
        _fill(
            client, _row(use_case="uc-a", cost_nanos=1000), _row(use_case="uc-b", cost_nanos=9000)
        )

        body = client.get("/v1beta/reporting?use_case=uc-a").json()

    assert body["totals"]["cost_nanos"] == 1000
    assert [row["key"] for row in body["by_use_case"]] == ["uc-a"]
    assert body["use_case"] == "uc-a"


def test_a_use_case_filter_cannot_widen_what_a_member_may_see() -> None:
    """**A filter narrows, never widens.**

    The one mistake here that matters: `scope = (use_case,)` written unconditionally reads as a
    narrowing and is a widening — every member of any use case could then name any other and be
    told its spend, from an endpoint whose whole job is to keep one team's figures out of
    another's screen.
    """
    caller = Principal(subject="alice", method="oidc", use_cases=("uc-a",))
    with _filtered_client(caller) as client:
        _fill(client, _row(use_case="uc-b", cost_nanos=9000))

        body = client.get("/v1beta/reporting?use_case=uc-b").json()

    assert body["totals"]["requests"] == 0
    assert body["totals"]["cost_nanos"] == 0
    assert body["in_scope"] is False


def test_an_empty_report_says_whether_it_was_allowed_to_be_full() -> None:
    """ "Nothing happened here" and "this is not yours to see" are both empty and are not the same
    fact. A screen that cannot tell them apart reports the second as the first."""
    caller = Principal(subject="alice", method="oidc", use_cases=("uc-a",))
    with _filtered_client(caller) as client:
        quiet = client.get("/v1beta/reporting?use_case=uc-a").json()
        forbidden = client.get("/v1beta/reporting?use_case=uc-b").json()

    assert quiet["totals"]["requests"] == 0 and quiet["in_scope"] is True
    assert forbidden["totals"]["requests"] == 0 and forbidden["in_scope"] is False


def test_an_unfiltered_report_says_it_was_not_narrowed() -> None:
    with _filtered_client() as client:
        body = client.get("/v1beta/reporting").json()

    assert body["use_case"] is None
    assert body["in_scope"] is True


def test_the_export_is_narrowed_by_the_same_filter_as_the_screen() -> None:
    """`FRD-602` §1 held once already and has to keep holding: the CSV is a **renderer over this
    same result**, so a filter applied to the report is applied to the file by construction. A
    second query here is how an export comes to return more than the screen it was exported
    from — as a file, which gets forwarded and cannot be recalled."""
    with _filtered_client() as client:
        _fill(client, _row(use_case="uc-a"), _row(use_case="uc-b"))

        body = client.get(
            "/v1beta/reporting?use_case=uc-a", headers={"Accept": "text/csv"}
        ).content.decode("utf-8")

    assert "uc-a" in body
    assert "uc-b" not in body


def test_a_malformed_use_case_filter_is_refused_by_name() -> None:
    with _filtered_client() as client:
        response = client.get("/v1beta/reporting?use_case=not a slug")

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
