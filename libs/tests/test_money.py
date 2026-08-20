"""Exact money arithmetic (FRD-403).

The point of these tests is that no amount ever passes through a float. A budget is a figure
somebody is accountable for; drift of a hundredth of a cent per request becomes a real
discrepancy over a million of them.
"""

from decimal import Decimal

import pytest

from aira_common.money import (
    NANOS_PER_UNIT,
    cost_nanos,
    format_amount,
    format_display,
    from_nanos,
    request_cost_nanos,
    to_nanos,
)


def test_amounts_convert_exactly_in_both_directions() -> None:
    assert to_nanos("1") == NANOS_PER_UNIT
    assert to_nanos("0.075") == 75_000_000
    assert to_nanos(Decimal("10.00")) == 10 * NANOS_PER_UNIT
    assert from_nanos(75_000_000) == Decimal("0.075000000")


def test_the_classic_float_trap_does_not_apply() -> None:
    # 0.1 + 0.2 != 0.3 in binary floating point. In nano-units it is exact.
    assert to_nanos("0.1") + to_nanos("0.2") == to_nanos("0.3")


def test_a_million_small_charges_do_not_drift() -> None:
    single = to_nanos("0.000001")
    assert single * 1_000_000 == to_nanos("1")


def test_rejects_something_that_is_not_an_amount() -> None:
    with pytest.raises(ValueError, match="not a valid amount"):
        to_nanos("kostenlos")


@pytest.mark.parametrize("amount", ["Infinity", "-Infinity", "NaN", "sNaN"])
def test_a_number_that_is_not_a_number_is_refused_in_the_documented_word(amount: str) -> None:
    """**`Decimal` accepts what money does not.**

    `Decimal("Infinity")` constructs happily and `quantize` then raises `InvalidOperation` — an
    `ArithmeticError`, out of a function whose one documented refusal is a `ValueError`, so a
    caller following the contract does not catch it. `"kostenlos"` above was the only input this
    ever asked about, and it is the one that happens to fail in the right place.

    Not hypothetical: `1e309` is exactly how `Infinity` gets into this system — JSON has no such
    literal, Python's parser produces one anyway, and `LESSONS.md` §1 records that same value
    costing a whole audit row one door along.
    """
    with pytest.raises(ValueError, match="not a valid amount"):
        to_nanos(amount)


def test_an_amount_too_large_to_state_exactly_is_refused_the_same_way() -> None:
    """Finite, and still not statable to the nano-unit. Same door, same word, different reason —
    which is why the message says which of the two it was."""
    with pytest.raises(ValueError, match="out of range"):
        to_nanos("1e400")


def test_prices_are_quoted_per_million_tokens() -> None:
    price = to_nanos("0.075")  # EUR per 1M tokens
    assert cost_nanos(1_000_000, price) == price
    assert cost_nanos(500_000, price) == price // 2
    assert cost_nanos(0, price) == 0
    assert cost_nanos(1000, 0) == 0


def test_input_and_output_are_priced_separately() -> None:
    # A model billing output at ten times input: a single "total tokens" figure could not
    # express this, which is the whole reason budgets moved to cost.
    input_price, output_price = to_nanos("1.00"), to_nanos("10.00")
    cheap = request_cost_nanos(1_000_000, 0, input_price, output_price)
    expensive = request_cost_nanos(0, 1_000_000, input_price, output_price)

    assert cheap == to_nanos("1.00")
    assert expensive == to_nanos("10.00")
    assert request_cost_nanos(1_000_000, 1_000_000, input_price, output_price) == to_nanos("11.00")


def test_display_rounds_like_an_invoice() -> None:
    assert format_amount(to_nanos("1.005")) == "1.01"
    assert format_amount(to_nanos("0.004")) == "0.00"
    assert format_amount(to_nanos("12.3456"), places=4) == "12.3456"


@pytest.mark.parametrize("amount", ["0.000255", "0.0004", "0.000001", "0.000000001"])
def test_a_small_amount_is_never_displayed_as_zero(amount: str) -> None:
    # "0.00" would claim nothing was spent — the one thing a spend figure must not do. The
    # contract is "not zero", not "full precision": it widens only as far as it has to.
    rendered = format_display(to_nanos(amount))
    assert Decimal(rendered) > 0, rendered


def test_ordinary_amounts_read_like_money_and_a_real_zero_stays_zero() -> None:
    assert format_display(to_nanos("12.345")) == "12.35"
    assert format_display(to_nanos("0.01")) == "0.01"
    assert format_display(0) == "0.00"
