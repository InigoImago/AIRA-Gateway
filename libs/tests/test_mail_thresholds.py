"""The thresholds a use case mails at (`FRD-409` FR-1), as both planes read them."""

from __future__ import annotations

import pytest

from aira_common.mail import (
    DEFAULT_BUDGET_MAIL_THRESHOLDS,
    MAX_BUDGET_MAIL_THRESHOLDS,
    ThresholdsInvalid,
    budget_mail_thresholds,
)


def test_the_default_is_the_moment_a_budget_is_used_up() -> None:
    assert DEFAULT_BUDGET_MAIL_THRESHOLDS == (100,)


def test_thresholds_are_kept_ascending() -> None:
    assert budget_mail_thresholds([100, 50, 80]) == [50, 80, 100]


def test_none_at_all_is_a_choice() -> None:
    assert budget_mail_thresholds([]) == []


def test_the_bounds_are_whole_percentages_from_one_to_a_hundred() -> None:
    assert budget_mail_thresholds([1, 100]) == [1, 100]


@pytest.mark.parametrize(
    "value",
    [
        [0],
        [101],
        [-5],
        [80.5],
        ["80"],
        [True],
        [None],
        "80,100",
        80,
        None,
        {"at": 80},
    ],
)
def test_anything_but_a_list_of_whole_percentages_is_refused_rather_than_dropped(
    value: object,
) -> None:
    with pytest.raises(ThresholdsInvalid):
        budget_mail_thresholds(value)


def test_a_threshold_named_twice_is_refused_and_says_which() -> None:
    with pytest.raises(ThresholdsInvalid, match="80 %"):
        budget_mail_thresholds([80, 80])


def test_more_than_a_handful_is_refused() -> None:
    allowed = list(range(10, 10 + MAX_BUDGET_MAIL_THRESHOLDS))
    assert budget_mail_thresholds(allowed) == allowed
    with pytest.raises(ThresholdsInvalid, match=str(MAX_BUDGET_MAIL_THRESHOLDS)):
        budget_mail_thresholds([*allowed, 99])
