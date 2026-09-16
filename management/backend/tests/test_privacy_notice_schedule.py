"""When the privacy notice must be acknowledged again (`FRD-625` FR-4), without a database."""

from __future__ import annotations

import datetime as dt

import pytest
from aira_management.apps.privacy.schedule import (
    Acknowledged,
    DueReason,
    NoticeMode,
    acknowledgement_due,
    month_start,
)

NOW = dt.datetime(2026, 9, 16, 10, 0, tzinfo=dt.UTC)
V = "2026-09-16.aaaa"


def _due(mode: NoticeMode, last: Acknowledged | None, *, any_: bool | None = None) -> object:
    return acknowledgement_due(mode, V, last, last is not None if any_ is None else any_, NOW)


@pytest.mark.parametrize("mode", [NoticeMode.ONCE, NoticeMode.MONTHLY])
def test_somebody_who_never_acknowledged_is_asked_as_a_first_reader(mode: NoticeMode) -> None:
    assert _due(mode, None) is DueReason.FIRST


@pytest.mark.parametrize("mode", [NoticeMode.ONCE, NoticeMode.MONTHLY])
def test_a_changed_notice_is_asked_again_in_every_mode(mode: NoticeMode) -> None:
    """They acknowledged a text that no longer exists — whatever the schedule, it is new to them."""
    assert _due(mode, None, any_=True) is DueReason.CHANGED


def test_once_asks_nothing_more_of_somebody_who_acknowledged_this_edition_long_ago() -> None:
    last = Acknowledged(V, NOW - dt.timedelta(days=400))
    assert _due(NoticeMode.ONCE, last) is None


def test_monthly_asks_again_at_the_first_start_of_a_new_month() -> None:
    last_month = Acknowledged(V, dt.datetime(2026, 8, 31, 23, 59, tzinfo=dt.UTC))
    this_month = Acknowledged(V, dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.UTC))

    assert _due(NoticeMode.MONTHLY, last_month) is DueReason.MONTH
    # The boundary itself counts as this month: midnight on the first is not "before" it.
    assert _due(NoticeMode.MONTHLY, this_month) is None


def test_always_asks_even_somebody_who_acknowledged_a_second_ago() -> None:
    assert _due(NoticeMode.ALWAYS, Acknowledged(V, NOW)) is DueReason.ALWAYS
    assert _due(NoticeMode.ALWAYS, None) is DueReason.ALWAYS


def test_an_acknowledgement_of_another_version_is_not_one_of_this_version() -> None:
    other = Acknowledged("2026-01-01.bbbb", NOW)
    assert _due(NoticeMode.ONCE, other, any_=True) is DueReason.CHANGED


def test_the_month_starts_at_midnight_utc_whatever_zone_the_clock_is_in() -> None:
    berlin = dt.timezone(dt.timedelta(hours=2))
    # 00:30 on 1 October in Berlin is still 30 September in UTC.
    assert month_start(dt.datetime(2026, 10, 1, 0, 30, tzinfo=berlin)) == dt.datetime(
        2026, 9, 1, tzinfo=dt.UTC
    )
