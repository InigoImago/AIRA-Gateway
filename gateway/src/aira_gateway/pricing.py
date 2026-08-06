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

from aira_common.money import request_cost_nanos
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import ModelRead


@dataclass(frozen=True, slots=True)
class Price:
    """A model's price per one million tokens, in nano-units, split by direction."""

    input_per_million_nanos: int
    output_per_million_nanos: int


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
        )

    async def cost_nanos(self, model: str, usage: CanonicalUsage | None) -> int | None:
        """Cost of one request in nano-units, or None when the model has no price.

        Input and output tokens are priced separately, which is why the canonical usage keeps
        them apart all the way from the upstream response instead of collapsing to a total.
        """
        if usage is None:
            return None
        price = await self.price_for(model)
        if price is None:
            return None
        return request_cost_nanos(
            usage.prompt_tokens,
            usage.completion_tokens,
            price.input_per_million_nanos,
            price.output_per_million_nanos,
        )
