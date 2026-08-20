"""Exact money arithmetic shared by both planes (FRD-403).

Money is never a float here. A single request can cost a fraction of a cent, and a month's
budget is the sum of millions of them — binary floating point would drift, and the drift would
land in a figure someone is accountable for.

Amounts are therefore integers in **nano-units** of the configured currency (1 unit = 10⁻⁹, so
1 EUR = 1_000_000_000). Integers are exact, they compare and accumulate without surprises, and
they behave identically on Postgres and on the SQLite the tests use — unlike ``NUMERIC``, which
SQLite stores as a float.

Prices are quoted the way providers quote them: per **one million tokens**.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NANOS_PER_UNIT = 1_000_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000

# Enough precision to state any realistic per-million price exactly (e.g. "0.075", "10.00").
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
        # **`Decimal` accepts what money does not.** `Decimal("Infinity")` and `Decimal("NaN")`
        # construct happily and then make `quantize` raise `InvalidOperation` — an
        # `ArithmeticError`, from a function whose one documented refusal is a `ValueError`, so a
        # caller following the contract does not catch it. That is the same value `LESSONS.md` §1
        # records costing an audit row: JSON has no `Infinity`, Python's parser produces one
        # anyway, and it travels as the string `"Infinity"` through exactly this door.
        raise ValueError(f"not a valid amount: {amount!r}")
    try:
        return int(value.quantize(_QUANTUM, rounding=ROUND_HALF_UP) * NANOS_PER_UNIT)
    except InvalidOperation as exc:
        # Finite and still not statable to the nano-unit — `1e400` is the shape. Refused in the
        # one word this function promises rather than raised as arithmetic nobody is catching.
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

    Two decimals is what people expect to read, but a small amount rounded to "0.00" says
    exactly the wrong thing — that nothing was spent. Where that would happen, show as many
    decimals as it takes to be truthful.
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

    Integer arithmetic throughout: the division truncates below one nano-unit, which is nine
    orders of magnitude below a cent and therefore cannot accumulate into a visible difference.
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

    Input and output tokens are billed at different rates by every provider AIRA talks to, which
    is why the gateway keeps the two apart all the way from the upstream response to here — a
    single ``total_tokens`` figure cannot be priced correctly at all.
    """
    return cost_nanos(prompt_tokens, input_price_per_million_nanos) + cost_nanos(
        completion_tokens, output_price_per_million_nanos
    )
