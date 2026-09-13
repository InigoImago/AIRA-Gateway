"""Exact money arithmetic shared by both planes (FRD-403).

Money is never a float: a month's budget sums millions of fractions of a cent, and binary floating
point would drift in a figure somebody is accountable for. Amounts are integers in **nano-units** of
the configured currency (1 EUR = 1_000_000_000) — exact, and identical on Postgres and on the SQLite
the tests use, unlike ``NUMERIC``. Prices are quoted per **one million tokens**, as providers quote
them.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NANOS_PER_UNIT = 1_000_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000

#: Enough precision to state any realistic per-million price exactly (e.g. "0.075", "10.00").
_QUANTUM = Decimal("0.000000001")


def to_nanos(amount: Decimal | int | str) -> int:
    """Convert a currency amount to nano-units.

    Accepts the decimal *string* form on purpose: JSON has no exact decimal type, so amounts
    cross service boundaries as strings and must not be routed through a float on the way.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a valid amount: {amount!r}") from exc
    if not value.is_finite():
        # `Decimal("Infinity")` and `Decimal("NaN")` construct, and `quantize` then raises
        # `InvalidOperation` — not the `ValueError` this function promises. JSON carries them in
        # as the strings Python's parser produces.
        raise ValueError(f"not a valid amount: {amount!r}")
    try:
        return int(value.quantize(_QUANTUM, rounding=ROUND_HALF_UP) * NANOS_PER_UNIT)
    except InvalidOperation as exc:
        # Finite and still not statable to the nano-unit (`1e400`).
        raise ValueError(f"amount is out of range: {amount!r}") from exc


def from_nanos(nanos: int) -> Decimal:
    """Convert nano-units back to a currency amount."""
    return (Decimal(nanos) / NANOS_PER_UNIT).quantize(_QUANTUM)


def format_amount(nanos: int, places: int = 2) -> str:
    """Render nano-units for display, rounded to ``places`` decimals (half-up, like an invoice)."""
    quantum = Decimal(1).scaleb(-places)
    return str(from_nanos(nanos).quantize(quantum, rounding=ROUND_HALF_UP))


def format_display(nanos: int, places: int = 2) -> str:
    """Render an amount for a human, never showing a non-zero amount as zero.

    A small amount rounded to "0.00" says that nothing was spent; where that would happen, show as
    many decimals as it takes to be truthful.
    """
    rendered = format_amount(nanos, places)
    if nanos == 0 or Decimal(rendered) != 0:
        return rendered
    for extra in (4, 6, 9):
        candidate = format_amount(nanos, extra)
        if Decimal(candidate) != 0:
            return candidate
    return rendered


def cost_nanos(tokens: int, price_per_million_nanos: int) -> int:
    """Cost of ``tokens`` at a price quoted per one million tokens, in nano-units.

    Integer arithmetic throughout: the division truncates below one nano-unit, nine orders of
    magnitude below a cent, so it cannot accumulate into a visible difference.
    """
    if tokens <= 0 or price_per_million_nanos <= 0:
        return 0
    return tokens * price_per_million_nanos // TOKENS_PER_PRICE_UNIT


def request_cost_nanos(
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_million_nanos: int,
    output_price_per_million_nanos: int,
) -> int:
    """Cost of one request, priced per direction.

    Input and output tokens are billed at different rates by every provider, which is why the two
    are kept apart from the upstream response to here — a single total cannot be priced.
    """
    return cost_nanos(prompt_tokens, input_price_per_million_nanos) + cost_nanos(
        completion_tokens, output_price_per_million_nanos
    )
