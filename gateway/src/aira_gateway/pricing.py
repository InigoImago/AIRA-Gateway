"""What a request cost (FRD-403).

The gateway is the only place that knows both halves of the calculation: the token split the
upstream reported, and the price the catalog carries for that model. Everything downstream — the
budget counters, the audit log, the consumption view — reads the number computed here.

A model without a price yields ``None``, which is deliberately not ``0``: "we do not know what
this cost" and "this was free" are different statements, and only one of them should be summed
into a spend figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import cost_nanos, request_cost_nanos
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import ModelRead


@dataclass(frozen=True, slots=True)
class Price:
    """A model's price per one million tokens, in nano-units, split by direction.

    Cached input and cache writes have their own rates where a provider publishes them
    (`FRD-133`): a read is 0.1x base input on Anthropic, a five-minute write 1.25x and an hour-long
    one 2x; Azure discounts reads and charges some writes. **Absent means the ordinary input
    rate** — the behaviour before these fields existed, and the conservative direction: a read
    priced at base over-states a little, where treating an unpriced write as free under-states in
    the direction a cost control must never be wrong.
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

        Input and output tokens are priced separately, which is why the canonical usage keeps
        them apart all the way from the upstream response instead of collapsing to a total — and
        since `FRD-133` the input side is three rates, not one: ordinary, cache read, cache write.
        A request that reports no cache tokens computes to exactly what it did before, because the
        uncached remainder is then the whole of the input.
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
            + cost_nanos(usage.cache_write_tokens, price.cache_write_rate)
        )
