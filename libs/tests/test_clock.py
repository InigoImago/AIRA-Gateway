"""The installation's clock (`FRD-629`): where its days, weeks and months begin.

Run in Europe/Berlin and across both of its clock changes, because the defect this guards against
is invisible in UTC: a CI runner counts in UTC, so a boundary worked out in the wrong zone, or by
adding 24 hours, passes every test written on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aira_common.clock import (
    TimeZoneError,
    day_ends,
    day_starts,
    local_date,
    month_ends,
    month_starts,
    read_in,
    week_ends,
    week_starts,
    zone_named,
)

BERLIN = zone_named("Europe/Berlin")


def test_utc_is_the_clock_of_an_installation_that_sets_none() -> None:
    assert zone_named("UTC") is UTC
    assert zone_named("") is UTC
    assert zone_named(" utc ") is UTC


@pytest.mark.parametrize("name", ["Europe/Berlinn", "CEST", "../etc/passwd"])
def test_a_name_the_database_does_not_know_is_refused_by_name(name: str) -> None:
    with pytest.raises(TimeZoneError, match="Europe/Berlin"):
        zone_named(name)


@pytest.mark.parametrize("name", ["Asia/Kolkata", "America/St_Johns", "Asia/Kathmandu"])
def test_a_zone_off_by_part_of_an_hour_is_refused_with_the_reason(name: str) -> None:
    """The series counts by the hour; a local midnight between two UTC hours would be counted a
    little wrong at every midnight."""
    with pytest.raises(TimeZoneError, match="whole hour"):
        zone_named(name)


def test_half_past_eleven_utc_is_tomorrow_in_berlin() -> None:
    """The owner's case: a request at 00:30 local time counted against yesterday."""
    moment = datetime(2026, 7, 14, 22, 30, tzinfo=UTC)

    assert local_date(moment, BERLIN).isoformat() == "2026-07-15"
    assert local_date(moment, UTC).isoformat() == "2026-07-14"
    assert day_starts(moment, BERLIN) == datetime(2026, 7, 14, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "hours"),
    [
        # 2026-03-29: the clocks go forward at 02:00, so the day has 23 hours.
        (datetime(2026, 3, 29, 12, 0, tzinfo=UTC), 23),
        # 2026-10-25: back at 03:00, so 25.
        (datetime(2026, 10, 25, 12, 0, tzinfo=UTC), 25),
        (datetime(2026, 10, 26, 12, 0, tzinfo=UTC), 24),
    ],
)
def test_a_day_ends_at_the_next_midnight_however_long_the_day(moment: datetime, hours: int) -> None:
    assert day_ends(moment, BERLIN) - day_starts(moment, BERLIN) == timedelta(hours=hours)
    assert day_ends(moment, BERLIN).astimezone(BERLIN).hour == 0


def test_a_week_begins_on_monday_in_the_zone() -> None:
    sunday_night_utc = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)  # Monday 00:30 in Berlin

    assert week_starts(sunday_night_utc, BERLIN) == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert week_ends(sunday_night_utc, BERLIN) == datetime(2026, 11, 1, 23, 0, tzinfo=UTC)
    assert week_starts(sunday_night_utc, UTC) == datetime(2026, 10, 19, tzinfo=UTC)


def test_a_month_begins_on_the_first_in_the_zone_and_across_the_year() -> None:
    new_years_eve = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)  # 1 January 00:30 in Berlin

    assert month_starts(new_years_eve, BERLIN) == datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert month_ends(new_years_eve, BERLIN) == datetime(2027, 1, 31, 23, 0, tzinfo=UTC)
    assert month_ends(datetime(2026, 12, 15, tzinfo=UTC), UTC) == datetime(2027, 1, 1, tzinfo=UTC)
    # October ends an hour later in UTC than September did: summer time ended in between.
    assert month_ends(datetime(2026, 10, 15, tzinfo=UTC), BERLIN) == datetime(
        2026, 10, 31, 23, 0, tzinfo=UTC
    )


def test_a_time_without_an_offset_is_read_on_the_installations_clock() -> None:
    assert read_in(datetime(2026, 7, 15), BERLIN) == datetime(2026, 7, 14, 22, 0, tzinfo=UTC)
    stated = datetime(2026, 7, 15, tzinfo=UTC)
    assert read_in(stated, BERLIN) is stated
