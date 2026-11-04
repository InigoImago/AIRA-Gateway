"""A count reads the same in English and in German (`aira_common.counts`)."""

from __future__ import annotations

import pytest

from aira_common.counts import GROUP_SEPARATOR, display_count


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (0, "0"),
        (999, "999"),
        (1000, "1 000"),
        (12840, "12 840"),
        (1234567, "1 234 567"),
        (-25000, "-25 000"),
    ],
)
def test_thousands_are_grouped_by_a_narrow_no_break_space(value: int, shown: str) -> None:
    assert display_count(value) == shown


def test_neither_a_comma_nor_a_point_is_written() -> None:
    """Each reads as the decimal mark in one of the two languages the console's readers use."""
    shown = display_count(987654321)

    assert "," not in shown and "." not in shown
    assert GROUP_SEPARATOR == " "
