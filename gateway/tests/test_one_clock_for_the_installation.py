"""The installation's clock (`FRD-629`): every period begins at `AIRA_TIME_ZONE`'s midnight.

Tested in Europe/Berlin, the clock this was asked for, and on the days its clocks change: a day
there is 23 or 25 hours long once a year each, which is exactly what arithmetic adding 24 hours gets
wrong on exactly those days. Every expectation is written out as a calendar fact — "00:30 on
15 July is the 15th" — rather than worked out with the code under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import format_display, to_nanos
from aira_gateway.api.usage import _windows
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets import keys
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage, ModelRead, RequestLog
from aira_gateway.reporting.series import axis
from aira_gateway.reporting.service import ReportingService

BERLIN = ZoneInfo("Europe/Berlin")

#: 00:30 on 15 July in Berlin, on summer time (UTC+2) — still the 14th in UTC.
HALF_PAST_MIDNIGHT = datetime(2026, 7, 14, 22, 30, tzinfo=UTC)

#: The night the clocks go back in 2026: 25 October, 03:00 summer time becomes 02:00 winter time,
#: at 01:00 UTC. The hour from 02:00 to 03:00 happens twice.
FIRST_TWO_OCLOCK = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30+02:00
SECOND_TWO_OCLOCK = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # 02:30+01:00

ANALYST = Principal(subject="s", method="oidc", roles=("it-steuerung",))
KEY = Principal(subject="erika", method="api_key", use_cases=("coding",), credential="aira_ab12")


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _row(
    when: datetime,
    *,
    latency: int = 40,
    cost: int = 1000,
    model: str = "chat-1",
    subject: str = "alice",
    prompt: int = 10,
) -> RequestLog:
    return RequestLog(
        subject=subject,
        username=subject,
        auth_method="api_key",
        use_case="uc",
        api="gemini",
        operation="generateContent",
        model=model,
        status=200,
        outcome="served",
        prompt_tokens=prompt,
        completion_tokens=20,
        total_tokens=prompt + 20,
        cost_nanos=cost,
        latency_ms=latency,
        created_at=when,
    )


async def _log(sessions: async_sessionmaker[AsyncSession], *rows: RequestLog) -> None:
    async with sessions() as session:
        session.add_all(rows)
        await session.commit()


def _seed(client: TestClient, *rows: RequestLog) -> None:
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: _log(client.app.state.db_sessionmaker, *rows))  # type: ignore[attr-defined]


def _client(
    principal: Principal, *, time_zone: str = "Europe/Berlin", **settings: Any
) -> TestClient:
    app = create_app(
        GatewaySettings(
            auth_required=False,
            oidc_enabled=False,
            log_queue_size=0,
            time_zone=time_zone,
            **settings,
        )
    )
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


# == the setting ================================================================================


def test_an_installation_that_sets_nothing_counts_in_utc() -> None:
    assert GatewaySettings().time_zone == "UTC"


@pytest.mark.parametrize(
    ("given", "kept"), [("utc", "UTC"), (" Europe/Berlin ", "Europe/Berlin"), ("", "UTC")]
)
def test_the_zone_is_kept_under_its_own_name(given: str, kept: str) -> None:
    """Every answer states the clock; it states it one way, whatever spelling the setting had."""
    assert GatewaySettings(time_zone=given).time_zone == kept


def test_a_zone_nobody_knows_is_refused_at_start_up_by_name() -> None:
    with pytest.raises(ValidationError, match="Mars/Olympus_Mons"):
        GatewaySettings(time_zone="Mars/Olympus_Mons")


def test_a_zone_half_an_hour_off_is_refused_rather_than_counted_wrong() -> None:
    """India's days begin at 18:30 UTC, between two of the hours the series counts by."""
    with pytest.raises(ValidationError, match="whole number of hours"):
        GatewaySettings(time_zone="Asia/Kolkata")


# == a budget's day and month ===================================================================


def test_half_past_midnight_is_today_on_the_installations_clock() -> None:
    assert keys.period_key("day", HALF_PAST_MIDNIGHT, BERLIN) == "2026-07-15"
    assert keys.period_key("day", HALF_PAST_MIDNIGHT) == "2026-07-14", "UTC stays the default"


def test_a_month_begins_at_the_installations_midnight() -> None:
    first_of_november = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)  # 00:30+01:00

    assert keys.period_key("month", first_of_november, BERLIN) == "2026-11"
    assert keys.period_key("month", first_of_november) == "2026-10"


def test_the_day_resets_at_the_next_midnight_there_written_with_its_offset() -> None:
    """The FRD's acceptance criterion: 22:30 UTC on 14 July counts against 15 July, and that day
    resets at 22:00 UTC on 15 July — which a client reads as midnight on the 16th."""
    assert keys.period_ends("day", HALF_PAST_MIDNIGHT, BERLIN) == datetime(
        2026, 7, 15, 22, 0, tzinfo=UTC
    )
    assert keys.resets_at("day", HALF_PAST_MIDNIGHT, BERLIN) == "2026-07-16T00:00:00+02:00"


def test_the_day_the_clocks_go_back_is_twenty_five_hours_long() -> None:
    start = keys.period_starts("day", FIRST_TWO_OCLOCK, BERLIN)
    end = keys.period_ends("day", FIRST_TWO_OCLOCK, BERLIN)

    assert end - start == timedelta(hours=25)
    assert keys.resets_at("day", FIRST_TWO_OCLOCK, BERLIN) == "2026-10-26T00:00:00+01:00"


def test_the_day_the_clocks_go_forward_is_twenty_three_hours_long() -> None:
    noon = datetime(2026, 3, 29, 10, 0, tzinfo=UTC)

    assert keys.period_ends("day", noon, BERLIN) - keys.period_starts(
        "day", noon, BERLIN
    ) == timedelta(hours=23)


def test_a_month_across_the_clock_change_ends_on_the_new_offset() -> None:
    mid_october = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

    assert keys.period_starts("month", mid_october, BERLIN) == datetime(
        2026, 9, 30, 22, 0, tzinfo=UTC
    )
    assert keys.resets_at("month", mid_october, BERLIN) == "2026-11-01T00:00:00+01:00"


async def test_a_budget_refuses_until_the_installations_midnight_and_says_so(sessions) -> None:
    """The whole path, not the helper: the counter is booked under the zone's date, the refusal
    names the zone's midnight, and that midnight — not UTC's — is when the next request passes."""
    async with sessions() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                subject="",
                period="day",
                limit_requests=1,
                enabled=True,
            )
        )
        await session.commit()
    service = BudgetService(sessions, zone=BERLIN)

    await service.record(
        (await service.guard("uc", "bob", HALF_PAST_MIDNIGHT)).budgets, 1, now=HALF_PAST_MIDNIGHT
    )
    with pytest.raises(BudgetExceeded) as refusal:
        await service.guard("uc", "bob", datetime(2026, 7, 15, 21, 59, tzinfo=UTC))
    assert refusal.value.resets_at == "2026-07-16T00:00:00+02:00"
    assert "It resets at 2026-07-16T00:00:00+02:00." in refusal.value.message

    # 00:00 on the 16th in Berlin, 22:00 on the 15th in UTC: a new day here, the same one there.
    assert (await service.guard("uc", "bob", datetime(2026, 7, 15, 22, 0, tzinfo=UTC))).budgets
    async with sessions() as session:
        keys_booked = (await session.execute(select(BudgetUsage.period_key))).scalars().all()
    assert keys_booked == ["2026-07-15"]


async def test_a_request_is_booked_under_the_installations_day_it_was_admitted_in(sessions) -> None:
    """Admitted at 00:30 in Berlin and settled later: the reservation carries the period it was
    admitted in, and that period is the installation's 15 July — UTC's would be the 14th."""
    async with sessions() as session:
        session.add(
            BudgetRead(
                id=1, use_case="uc", scope="use_case", subject="", period="day", enabled=True
            )
        )
        await session.commit()
    service = BudgetService(sessions, zone=BERLIN)

    reservation = await service.guard("uc", "bob", HALF_PAST_MIDNIGHT)
    await service.settle(reservation, 10, now=HALF_PAST_MIDNIGHT + timedelta(minutes=5))

    async with sessions() as session:
        keys_booked = (await session.execute(select(BudgetUsage.period_key))).scalars().all()
    assert reservation.period_keys == {1: "2026-07-15"}
    assert keys_booked == ["2026-07-15"]


async def test_what_is_left_says_when_it_comes_back_on_the_installations_clock(sessions) -> None:
    async with sessions() as session:
        session.add(
            BudgetRead(
                id=1, use_case="uc", scope="use_case", subject="", period="month", enabled=True
            )
        )
        await session.commit()

    (row,) = await BudgetService(sessions, zone=BERLIN).usage("uc", HALF_PAST_MIDNIGHT)

    assert row["period_ends"] == "2026-08-01T00:00:00+02:00"


# == a caller's today, week and month ===========================================================


def test_a_callers_week_and_month_begin_on_the_installations_clock() -> None:
    """00:30 on Monday 2 November in Berlin, still Sunday 1 November in UTC: the day, the week and
    the month are all the Monday's — where in UTC the week began on 26 October."""
    today, week, month = _windows(datetime(2026, 11, 1, 23, 30, tzinfo=UTC), BERLIN)

    midnight = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)
    assert today == (midnight, datetime(2026, 11, 2, 23, 0, tzinfo=UTC))
    assert week == (midnight, datetime(2026, 11, 8, 23, 0, tzinfo=UTC))
    assert month == (
        datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
        datetime(2026, 11, 30, 23, 0, tzinfo=UTC),
    )


def test_the_allowance_endpoint_names_its_clock_and_writes_every_moment_on_it() -> None:
    with _client(KEY) as client:
        body = client.get("/v1beta/me/budget").json()

    assert body["time_zone"] == "Europe/Berlin"
    for name in ("today", "this_week", "this_period"):
        for end in ("from", "to"):
            moment = datetime.fromisoformat(body[name][end])
            assert moment.utcoffset() == moment.astimezone(BERLIN).utcoffset(), (name, end)
            local = moment.astimezone(BERLIN)
            assert (local.hour, local.minute) == (0, 0), (name, end, body[name][end])
    assert datetime.fromisoformat(body["today"]["from"]).date() == datetime.now(BERLIN).date()


def test_an_installation_that_sets_nothing_says_utc() -> None:
    with _client(KEY, time_zone="UTC") as client:
        body = client.get("/v1beta/me/budget").json()

    assert body["time_zone"] == "UTC"
    assert body["today"]["from"].endswith("+00:00")


# == reports ====================================================================================


def test_a_reports_bare_dates_are_the_installations_days() -> None:
    """`2026-10-01` is midnight in Berlin: the request at 00:30 that night is October's, the one at
    00:30 on 1 November is not, and the window says both ends with their offsets."""
    with _client(ANALYST) as client:
        _seed(
            client,
            _row(datetime(2026, 9, 30, 21, 30, tzinfo=UTC)),  # 23:30 on 30 September
            _row(datetime(2026, 9, 30, 22, 30, tzinfo=UTC)),  # 00:30 on 1 October
            _row(datetime(2026, 10, 31, 23, 30, tzinfo=UTC)),  # 00:30 on 1 November
        )
        body = client.get(
            "/v1beta/reporting", params={"from": "2026-10-01", "to": "2026-11-01", "series": "day"}
        ).json()

    assert body["totals"]["requests"] == 1
    assert (body["from"], body["to"]) == ("2026-10-01T00:00:00+02:00", "2026-11-01T00:00:00+01:00")
    series = body["series"]
    assert series["time_zone"] == "Europe/Berlin"
    assert (len(series["buckets"]), series["buckets"][0], series["buckets"][-1]) == (
        31,
        "2026-10-01",
        "2026-10-31",
    )
    assert [point["bucket"] for point in series["points"]] == ["2026-10-01"]


def test_a_report_without_a_window_is_the_installations_month() -> None:
    with _client(ANALYST) as client:
        body = client.get("/v1beta/reporting").json()

    start = datetime.fromisoformat(body["from"]).astimezone(BERLIN)
    assert (start.day, start.hour, start.minute) == (1, 0, 0)
    assert start.month == datetime.now(BERLIN).month


def test_an_export_is_named_by_the_installations_dates() -> None:
    """In UTC the window above begins on 30 September and ends on 31 October."""
    with _client(ANALYST) as client:
        usage = client.get(
            "/v1beta/reporting",
            params={"from": "2026-10-01", "to": "2026-11-01"},
            headers={"Accept": "text/csv"},
        )
        register = client.get(
            "/v1beta/register",
            params={"from": "2026-10-01", "to": "2026-11-01"},
            headers={"Accept": "text/csv"},
        )

    assert "aira-usage_use_case_2026-10-01_2026-11-01.csv" in usage.headers["content-disposition"]
    assert "aira-register_2026-10-01_2026-11-01.csv" in register.headers["content-disposition"]
    assert "2026-10-01T00:00:00+02:00 to 2026-11-01T00:00:00+01:00" in register.text


def test_the_register_states_its_window_on_the_installations_clock() -> None:
    with _client(ANALYST) as client:
        body = client.get("/v1beta/register", params={"from": "2026-10-01", "to": "2026-11-01"})

    assert (body.json()["from"], body.json()["to"]) == (
        "2026-10-01T00:00:00+02:00",
        "2026-11-01T00:00:00+01:00",
    )


# == the series: UTC hours folded into the installation's days and hours ========================


async def _series(sessions, start: datetime, end: datetime, granularity: str) -> dict[str, Any]:
    report = await ReportingService(sessions).report(
        None, start, end, granularity=granularity, zone=BERLIN
    )
    return report


async def test_the_day_the_clocks_go_back_is_one_column_of_twenty_five_hours(sessions) -> None:
    """Its first and its last half hour are a day apart in no clock but UTC's."""
    await _log(
        sessions,
        _row(datetime(2026, 10, 24, 22, 30, tzinfo=UTC)),  # 00:30+02:00 on the 25th
        _row(datetime(2026, 10, 25, 22, 30, tzinfo=UTC)),  # 23:30+01:00 on the 25th
        _row(datetime(2026, 10, 25, 23, 30, tzinfo=UTC)),  # 00:30+01:00 on the 26th
    )

    report = await _series(
        sessions,
        datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        datetime(2026, 10, 26, 23, 0, tzinfo=UTC),
        "day",
    )

    series = report["series"]
    assert series["buckets"] == ["2026-10-25", "2026-10-26"]
    assert [(point["bucket"], point["requests"]) for point in series["points"]] == [
        ("2026-10-25", 2),
        ("2026-10-26", 1),
    ]
    assert sum(point["requests"] for point in series["points"]) == report["totals"]["requests"]


async def test_the_hour_that_happens_twice_is_two_columns(sessions) -> None:
    await _log(sessions, _row(FIRST_TWO_OCLOCK), _row(SECOND_TWO_OCLOCK))

    series = (
        await _series(
            sessions,
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
            "hour",
        )
    )["series"]

    assert len(series["buckets"]) == 25
    assert series["buckets"][2:5] == [
        "2026-10-25T02+02:00",
        "2026-10-25T02+01:00",
        "2026-10-25T03+01:00",
    ]
    assert [point["bucket"] for point in series["points"]] == [
        "2026-10-25T02+02:00",
        "2026-10-25T02+01:00",
    ]


def test_the_hour_the_clocks_skip_is_no_column() -> None:
    hours = axis(
        datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
        "hour",
        BERLIN,
    )

    assert len(hours) == 23
    assert hours[1:3] == ["2026-03-29T01+01:00", "2026-03-29T03+02:00"]


async def test_hours_folded_into_a_day_add_up_and_average_by_request(sessions) -> None:
    """One request at 500 ms in one hour and three at 100 ms in a later one are 200 ms on average —
    an average of the two hours' averages would say 300 — and the slowest is the earlier hour's."""
    morning = datetime(2026, 8, 4, 6, 15, tzinfo=UTC)
    await _log(
        sessions,
        _row(morning, latency=500, cost=to_nanos("2.50")),
        *(_row(morning + timedelta(hours=9), latency=100, cost=to_nanos("1.00")) for _ in range(3)),
    )

    (point,) = (
        await _series(
            sessions,
            datetime(2026, 8, 3, 22, 0, tzinfo=UTC),
            datetime(2026, 8, 4, 22, 0, tzinfo=UTC),
            "day",
        )
    )["series"]["points"]

    assert point["bucket"] == "2026-08-04"
    assert (point["requests"], point["total_tokens"]) == (4, 120)
    # The displayed amount is the folded sum's, not the first hour's carried along.
    assert (point["cost_nanos"], point["cost"]) == (
        to_nanos("5.50"),
        format_display(to_nanos("5.50")),
    )
    assert (point["avg_latency_ms"], point["max_latency_ms"]) == (200, 500)


async def test_a_series_on_another_clock_is_scoped_like_every_other_figure(sessions) -> None:
    """The folding reads its own hours from the database: the scope must bound that query too."""
    await _log(sessions, _row(datetime(2026, 8, 4, 6, 0, tzinfo=UTC)))
    async with sessions() as session:
        elsewhere = _row(datetime(2026, 8, 4, 7, 0, tzinfo=UTC))
        elsewhere.use_case = "another"
        session.add(elsewhere)
        await session.commit()

    report = await ReportingService(sessions).report(
        ("uc",),
        datetime(2026, 8, 3, 22, 0, tzinfo=UTC),
        datetime(2026, 8, 4, 22, 0, tzinfo=UTC),
        granularity="day",
        zone=BERLIN,
    )

    assert [point["requests"] for point in report["series"]["points"]] == [1]


async def test_a_band_keeps_its_own_column_when_hours_are_folded(sessions) -> None:
    await _log(
        sessions,
        _row(datetime(2026, 8, 4, 6, 0, tzinfo=UTC), model="chat-1"),
        _row(datetime(2026, 8, 4, 9, 0, tzinfo=UTC), model="chat-1"),
        _row(datetime(2026, 8, 4, 9, 0, tzinfo=UTC), model="chat-2"),
    )

    points = (
        await _series(
            sessions,
            datetime(2026, 8, 3, 22, 0, tzinfo=UTC),
            datetime(2026, 8, 4, 22, 0, tzinfo=UTC),
            "day",
        )
    )["series"]["points"]

    assert sorted((point["key"], point["requests"]) for point in points) == [
        ("chat-1", 2),
        ("chat-2", 1),
    ]


# == KIRA's usage window ========================================================================


def test_kira_dates_without_an_offset_are_the_installations_days() -> None:
    principal = Principal(subject="ada", method="oidc", username="ada", use_cases=("uc",))
    with _client(principal) as client:
        _seed(
            client,
            ModelRead(model="chat-1", numeric_id=1004, capabilities=["generate"]),
            # 00:30 on 1 October, and 00:30 on 1 November.
            _row(datetime(2026, 9, 30, 22, 30, tzinfo=UTC), subject="ada", prompt=1),
            _row(datetime(2026, 10, 31, 23, 30, tzinfo=UTC), subject="ada", prompt=2),
        )
        dates = client.get("/kira/api/external/ki-usage?startDatum=2026-10-01&endDatum=2026-11-01")
        start = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
        end = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())
        seconds = client.get(f"/kira/api/external/ki-usage?startDatum={start}&endDatum={end}")

    assert dates.status_code == 200, dates.text
    assert [row["token_input_sum"] for row in dates.json()] == [1]
    # Seconds count from a moment and need no clock: this window is UTC's October, which holds the
    # request on the 31st at 23:30 UTC and not the one on the 30th.
    assert [row["token_input_sum"] for row in seconds.json()] == [2]
