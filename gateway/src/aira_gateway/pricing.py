"""What a request cost (FRD-403).

The gateway alone knows both halves: the token split the upstream reported and the price the
catalog carries. Everything downstream — budget counters, audit log, consumption view — reads the
number computed here.

A model without a price yields ``None``, never ``0``: "we do not know what this cost" and "this was
free" are different statements, and only one belongs in a spend figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import cost_nanos, request_cost_nanos
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import ModelRead

#: What writing a prefix into a cache for **an hour** costs, as a multiple of the ordinary input
#: price. Anthropic's rule, the one provider that offers the hour: twice the input price, against
#: 1.25x for five minutes, which is what a model's single cache-write price states.
LONG_CACHE_WRITE_MULTIPLIER = 2


@dataclass(frozen=True, slots=True)
class Price:
    """A model's price per one million tokens, in nano-units, split by direction.

    Cache reads and writes have their own rates where a provider publishes them (`FRD-133`).
    **Absent means the ordinary input rate** — the conservative direction: a read priced at base
    over-states a little, while an unpriced write treated as free would under-state.
    """

    input_per_million_nanos: int
    output_per_million_nanos: int
    cached_input_per_million_nanos: int | None = None
    cache_write_per_million_nanos: int | None = None

    @property
    def cached_input_rate(self) -> int:
        return (
            self.cached_input_per_million_nanos
            if self.cached_input_per_million_nanos is not None
            else self.input_per_million_nanos
        )

    @property
    def cache_write_rate(self) -> int:
        return (
            self.cache_write_per_million_nanos
            if self.cache_write_per_million_nanos is not None
            else self.input_per_million_nanos
        )

    @property
    def long_cache_write_rate(self) -> int:
        """An hour's cache write: the provider's fixed multiple of input, and never below the
        five-minute rate the catalogue states."""
        return max(
            self.input_per_million_nanos * LONG_CACHE_WRITE_MULTIPLIER, self.cache_write_rate
        )


class PricingService:
    """Resolves model prices from the read-model and prices a request's token usage."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def price_for(self, model: str) -> Price | None:
        """The price on file for ``model``, or None if it has none."""
        async with self._sessionmaker() as session:
            record = await session.get(ModelRead, model)
        if (
            record is None
            or record.input_price_per_million_nanos is None
            or record.output_price_per_million_nanos is None
        ):
            return None
        return Price(
            input_per_million_nanos=record.input_price_per_million_nanos,
            output_per_million_nanos=record.output_price_per_million_nanos,
            cached_input_per_million_nanos=record.cached_input_price_per_million_nanos,
            cache_write_per_million_nanos=record.cache_write_price_per_million_nanos,
        )

    async def cost_nanos(self, model: str, usage: CanonicalUsage | None) -> int | None:
        """Cost of one request in nano-units, or None when the model has no price.

        Input and output are priced separately, and the input side at three rates (ordinary, cache
        read, cache write — `FRD-133`). A request reporting no cache tokens computes exactly as
        before, since the uncached remainder is then the whole input.
        """
        if usage is None:
            return None
        price = await self.price_for(model)
        if price is None:
            return None
        return (
            request_cost_nanos(
                usage.uncached_input_tokens,
                usage.completion_tokens,
                price.input_per_million_nanos,
                price.output_per_million_nanos,
            )
            + cost_nanos(usage.cached_input_tokens, price.cached_input_rate)
            + cost_nanos(usage.cache_write_tokens - long_writes(usage), price.cache_write_rate)
            + cost_nanos(long_writes(usage), price.long_cache_write_rate)
        )


def long_writes(usage: CanonicalUsage) -> int:
    """The hour's cache writes, clamped into the writes they are part of: a provider reporting more
    of them than writes cannot make the five-minute share negative."""
    return min(max(0, usage.long_cache_write_tokens), max(0, usage.cache_write_tokens))
