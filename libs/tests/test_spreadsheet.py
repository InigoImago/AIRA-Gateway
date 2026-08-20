"""A cell that a spreadsheet would run rather than read (`aira_common.spreadsheet`).

The rule lives in `aira_common` because **two** exports need it and neither owns it: the gateway's
usage report and Management's smoke-test evaluation. The second was written after the first and
copied its conventions by name — the BOM, the CRLF, the quoting — and could not copy this one,
because it was in neither.

The end-to-end proofs are where the content comes from: `gateway/tests/test_csv_export.py` drives a
caller's refused request into the usage export, and `management/backend/tests/test_smoketests.py`
puts a model's own answer into the evaluation. This file is about the rule itself.
"""

from __future__ import annotations

import pytest

from aira_common.spreadsheet import FORMULA_STARTERS, safe_cell


@pytest.mark.parametrize("starter", FORMULA_STARTERS)
def test_every_character_the_module_names_is_actually_neutralised(starter: str) -> None:
    """Asserted over the constant rather than over a list written out again here.

    A hand-written copy is a list that stops matching the day somebody adds a character to the
    real one — the shape `LESSONS.md` §1 records six times — and it would fail *silently*, by
    testing a smaller set than the module claims.
    """
    cell = safe_cell(f"{starter}danger")

    assert isinstance(cell, str)
    assert cell == f"'{starter}danger"
    assert not cell.startswith(FORMULA_STARTERS)


def test_the_starters_are_the_ones_a_spreadsheet_acts_on() -> None:
    """A guard on the guard: the parametrisation above passes vacuously if the tuple empties, and
    it says nothing about *which* characters are in it."""
    assert set("=+-@") <= set(FORMULA_STARTERS)
    assert {"\t", "\r"} <= set(FORMULA_STARTERS), "a hidden leading character is the same trick"


@pytest.mark.parametrize(
    "value",
    ["kundenservice", "gemini-2.5-flash", "vertrieb, süd", "", "0.00", "1e3", "'already text"],
)
def test_an_ordinary_value_is_returned_exactly_as_it_was(value: str) -> None:
    """The narrowing this must not do. A reader comparing an export against a screen has to find
    the same string, and `'already text` must not collect a second quote on the way through."""
    assert safe_cell(value) == value


@pytest.mark.parametrize("value", [0, 12, -5, 1.5, None, True])
def test_something_that_is_not_a_string_is_left_alone(value: object) -> None:
    """`csv.writer` writes a number as a number, and a number has nothing for a formula to begin
    with. Prefixing one would turn a figure into text in the column somebody wants to sum."""
    assert safe_cell(value) is value
