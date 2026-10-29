"""The installation's clock: where its days, weeks and months begin (`FRD-629`, `ADR-0049`).

One time zone for the installation, `AIRA_TIME_ZONE`, set on both planes: a budget's day and month,
a report's days and hours, the console's every timestamp and the budget a caller is shown all
begin and end on this clock. UTC by default, so an installation that sets nothing counts as it
always did.

**A local day is not 24 hours.** In a zone with summer time one day of the year has 23 hours and
one has 25, so a boundary is always worked out as a **date** in the zone and its midnight turned
into an instant — never by adding a day's worth of seconds to the last one. Every boundary is
returned as an instant in UTC, because that is the only form in which subtracting two of them gives
the time between them (`midnight`).

**A whole number of hours from UTC, at every moment.** The usage series counts by the hour in the
database and folds those hours into the zone's days (`reporting.series`), which is exact only where
every local day begins on a UTC hour. A zone half an hour off — India's, Iran's, Newfoundland's — is
refused by name rather than counted a little wrong at every midnight.
"""

from __future__ import annotations

import functools
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The clock an installation that sets nothing counts on — the one it always counted on.
DEFAULT_TIME_ZONE = "UTC"

#: The years a zone's offsets are checked across: from before any row this system holds to well
#: past any release of it, so a change of rule a zone has announced is read too.
_CHECKED_YEARS = range(2000, 2051)


class TimeZoneError(ValueError):
    """A name that cannot be the installation's clock, and why. Raised at start-up."""


@functools.lru_cache(maxsize=16)
def zone_named(name: str) -> tzinfo:
    """The zone ``name`` names — an IANA name such as ``Europe/Berlin``, or ``UTC``.

    Refused by name: one this system's time-zone database does not know, and one whose offset from
    UTC is not a whole number of hours at some moment of :data:`_CHECKED_YEARS` (see the module).
    """
    cleaned = name.strip()
    if not cleaned or cleaned.upper() == DEFAULT_TIME_ZONE:
        return UTC
    try:
        zone = ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimeZoneError(
            f"'{name}' is not a time zone this installation knows. Name one as the IANA database "
            "does, such as Europe/Berlin, or UTC."
        ) from exc
    for year in _CHECKED_YEARS:
        for month in range(1, 13):
            offset = datetime(year, month, 15, tzinfo=UTC).astimezone(zone).utcoffset()
            if offset is not None and offset % timedelta(hours=1):
                raise TimeZoneError(
                    f"'{name}' is {offset} from UTC on {year}-{month:02d}-15: an installation's "
                    "days must begin on a whole hour of UTC, because the usage series counts by "
                    "the hour. Choose a zone a whole number of hours from UTC."
                )
    return zone


def local_date(moment: datetime, zone: tzinfo) -> date:
    """The calendar date ``moment`` falls on in ``zone``."""
    return moment.astimezone(zone).date()


def midnight(day: date, zone: tzinfo) -> datetime:
    """The first moment of ``day`` in ``zone`` — **as an instant in UTC**.

    Not in ``zone``: Python subtracts two datetimes that share a zone by their wall clocks, so the
    day the clocks go back would measure 24 hours, and every window, limit and axis computed from
    such a pair would be an hour out. An instant in UTC subtracts as elapsed time. Shown in the
    zone where it is shown (`astimezone`), and nowhere else.
    """
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


def day_starts(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the day ``moment`` falls in."""
    return midnight(local_date(moment, zone), zone)


def day_ends(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the next day — 23 or 25 hours later on the days the clocks change."""
    return midnight(local_date(moment, zone) + timedelta(days=1), zone)


def week_starts(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the week ``moment`` falls in: Monday, as ISO 8601 has it."""
    today = local_date(moment, zone)
    return midnight(today - timedelta(days=today.weekday()), zone)


def week_ends(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the next week."""
    today = local_date(moment, zone)
    return midnight(today + timedelta(days=7 - today.weekday()), zone)


def month_starts(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the month ``moment`` falls in."""
    return midnight(local_date(moment, zone).replace(day=1), zone)


def month_ends(moment: datetime, zone: tzinfo) -> datetime:
    """The first moment of the next month."""
    today = local_date(moment, zone)
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return midnight(date(year, month, 1), zone)


def read_in(value: datetime, zone: tzinfo) -> datetime:
    """``value`` as an instant: one with an offset as it says, one without as a time in ``zone``."""
    return value if value.tzinfo is not None else value.replace(tzinfo=zone)
