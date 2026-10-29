"""What a request cost (FRD-403).

The gateway alone knows both halves: the token split the upstream reported and the price the
catalog carries. Everything downstream — budget counters, audit log, consumption view — reads the
number computed here.

A model without a price yields ``None``, never ``0``: "we do not know what this cost" and "this was
free" are different statements, and only one belongs in a spend figure.

**A long prompt can cost more per token** (`FRD-408`). Google and Anthropic bill a request whose
prompt is longer than a stated length — 200 000 tokens on every model that has such a price — at a
second set of rates, for **all** of its tokens, input and output alike. A catalogue entry may carry
that length and those rates; a request is priced at the set its own prompt selects, and the
reservation before it at the set its estimated prompt selects.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import cost_nanos, request_cost_nanos
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import ModelRead

#: What writing a prefix into a cache for **five minutes** costs, as a fraction of the ordinary
#: input price, where the catalogue states no cache-write price: Anthropic's rate, 1.25x. Anthropic
#: is the one provider that reports cache writes (`cache_creation_input_tokens`) — Gemini's implicit
#: cache and the OpenAI dialect report none — so this is the rate every reported write is billed at,
#: and the ordinary input rate under-states it by a quarter.
CACHE_WRITE_NUMERATOR, CACHE_WRITE_DENOMINATOR = 5, 4

#: What writing a prefix into a cache for **an hour** costs, as a multiple of the ordinary input
#: price. Anthropic's rule, the one provider that offers the hour: twice the input price, against
#: 1.25x for five minutes, which is what a model's single cache-write price states.
LONG_CACHE_WRITE_MULTIPLIER = 2


@dataclass(frozen=True, slots=True)
class Rates:
    """One set of prices per one million tokens, in nano-units, split by direction.

    Cache reads and writes have their own rates where a provider publishes them (`FRD-133`).
    Absent, a **read** is charged at the ordinary input rate, which can only over-state it, and a
    **write** at 1.25x that rate (:data:`CACHE_WRITE_NUMERATOR`), which is what the one provider
    that reports writes bills for them.
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
        if self.cache_write_per_million_nanos is not None:
            return self.cache_write_per_million_nanos
        return self.input_per_million_nanos * CACHE_WRITE_NUMERATOR // CACHE_WRITE_DENOMINATOR

    @property
    def long_cache_write_rate(self) -> int:
        """An hour's cache write: the provider's fixed multiple of input, and never below the
        five-minute rate the catalogue states."""
        return max(
            self.input_per_million_nanos * LONG_CACHE_WRITE_MULTIPLIER, self.cache_write_rate
        )

    def cost_nanos(self, usage: CanonicalUsage) -> int:
        """``usage`` at these rates: input and output apart, and the input side at three rates
        (ordinary, cache read, cache write — `FRD-133`). A request reporting no cache tokens
        computes exactly as before, since the uncached remainder is then the whole input."""
        return (
            request_cost_nanos(
                usage.uncached_input_tokens,
                usage.completion_tokens,
                self.input_per_million_nanos,
                self.output_per_million_nanos,
            )
            + cost_nanos(usage.cached_input_tokens, self.cached_input_rate)
            + cost_nanos(usage.cache_write_tokens - long_writes(usage), self.cache_write_rate)
            + cost_nanos(long_writes(usage), self.long_cache_write_rate)
        )


@dataclass(frozen=True, slots=True)
class Price(Rates):
    """A model's price: its rates, and — where its provider charges more for a long prompt — the
    prompt length above which a second set applies, and that set (`FRD-408`)."""

    #: The longest prompt, in tokens, the ordinary rates apply to; ``None`` for one set at every
    #: length.
    long_context_threshold: int | None = None
    long_context: Rates | None = None

    def rates_for(self, prompt_tokens: int) -> Rates:
        """The rates a request whose prompt is ``prompt_tokens`` long is billed at — every token
        of it, the answer included, as both providers that charge by length bill it."""
        if (
            self.long_context is not None
            and self.long_context_threshold is not None
            and prompt_tokens > self.long_context_threshold
        ):
            return self.long_context
        return self


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
            long_context_threshold=record.long_context_threshold,
            long_context=_long_context_rates(record),
        )

    async def cost_nanos(self, model: str, usage: CanonicalUsage | None) -> int | None:
        """Cost of one request in nano-units, or None when the model has no price.

        Priced at the rates its prompt selects (:meth:`Price.rates_for`): the prompt is every input
        token the provider counted — cached and cache-written included, as both providers measure
        the length that moves a request to its second set of rates.
        """
        if usage is None:
            return None
        price = await self.price_for(model)
        if price is None:
            return None
        return price.rates_for(usage.prompt_tokens).cost_nanos(usage)


def _long_context_rates(record: ModelRead) -> Rates | None:
    """The rates above the catalogue's prompt length, or ``None`` where it states no such set.

    A length with half a set is no set: the catalogue refuses it where it is typed, and an event
    that carries one anyway is read as the ordinary rates rather than as a price nobody stated.
    """
    if (
        record.long_context_threshold is None
        or record.long_context_input_price_per_million_nanos is None
        or record.long_context_output_price_per_million_nanos is None
    ):
        return None
    return Rates(
        input_per_million_nanos=record.long_context_input_price_per_million_nanos,
        output_per_million_nanos=record.long_context_output_price_per_million_nanos,
        cached_input_per_million_nanos=record.long_context_cached_input_price_per_million_nanos,
        cache_write_per_million_nanos=record.long_context_cache_write_price_per_million_nanos,
    )


def long_writes(usage: CanonicalUsage) -> int:
    """The hour's cache writes, clamped into the writes they are part of: a provider reporting more
    of them than writes cannot make the five-minute share negative."""
    return min(max(0, usage.long_cache_write_tokens), max(0, usage.cache_write_tokens))
