"""What a stream used when its vendor never said, established from facts (`FRD-128` FR-6).

A vendor that reports usage only in a stream's last chunk — Vertex's Gemini does — says nothing
about a stream whose caller left before it: the prompt was read and an answer had begun, both
billed, and the request would otherwise be booked as having cost nothing. Nothing here is
estimated. Two things are known for certain and counted by the vendor's own tokenizer, at an
endpoint it does not charge for: **the request as it was sent**, and **the answer as far as it was
received**. Neither the answer the vendor went on producing after the caller left nor its thinking
can be seen, so the figure is a floor — and it is priced as one:

- Google caches a long prompt on its own and bills the cached part at a lower rate, which only the
  last chunk would have said. A prompt shorter than :data:`IMPLICIT_CACHE_MIN_TOKENS` is never
  cached, so its full rate is exact; a longer one is priced as cached, so the booking never exceeds
  what the vendor charged.

The row says how its usage was arrived at (:class:`~aira_gateway.audit.UsageBasis`), so a report
can say its spend is a lower bound, and stores the two counts alone: a cache split or a thinking
count it never saw stays unknown there, and only the price takes the floor. A vendor with no such
endpoint, a count that fails, or an installation that turned the second look at the text off
(`AIRA_COUNT_CANCELLED_STREAMS`) leaves the usage unknown — never zero.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aira_common.logging import get_logger
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
    TextPart,
    ToolCallPart,
)
from aira_gateway.upstreams.base import Countable, Upstream, can_count

_log = get_logger("aira_gateway")

#: How long both counts together may take. The caller has gone, so nobody waits for it; the bound
#: is on how long the request's task outlives them.
COUNT_TIMEOUT_SECONDS = 5.0

#: The shortest prompt Google caches on its own on any Gemini model it documents. Below it the whole
#: prompt is billed at the ordinary rate, which makes that rate exact; at or above it the prompt is
#: priced as cached. The smallest documented minimum is taken: a larger one would price a prompt
#: Google did cache at the ordinary rate, above the bill.
IMPLICIT_CACHE_MIN_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class Unreported:
    """A stream the caller left before its vendor reported what it used."""

    #: The adapter that answered.
    provider: Upstream
    #: The request exactly as it was sent to that adapter.
    sent: CanonicalRequest
    #: The model's own text as received — never a notice this gateway put in front of it.
    answer: str
    #: The function calls received.
    calls: tuple[ToolCallPart, ...]
    #: Where the answer came from, so the text is counted where it was sent.
    region: str


async def count_unreported(unreported: Unreported) -> CanonicalUsage | None:
    """The request and the received answer as the vendor counts them, or ``None`` — unknown.

    Never raises: a count that fails leaves the usage unknown, which is what it was before anybody
    asked, and the row and the booking go ahead without it.
    """
    provider = unreported.provider
    if not can_count(provider):
        return None
    try:
        async with asyncio.timeout(COUNT_TIMEOUT_SECONDS):
            prompt = await provider.count_tokens(unreported.sent, region=unreported.region)
            answer = await _answer_tokens(provider, unreported)
    except Exception as exc:  # noqa: BLE001 — unknown is the honest result of any failure here
        _log.warning(
            "unreported_usage_not_counted",
            model=unreported.sent.model,
            error_type=type(exc).__name__,
        )
        return None
    return CanonicalUsage(prompt_tokens=prompt, completion_tokens=answer)


def floor_priced(usage: CanonicalUsage) -> CanonicalUsage:
    """``usage`` as a counted row is **priced**: a prompt long enough for Google to have cached it
    is taken as cached, so the price is never above the bill. Only the price — the row stores the
    counts and leaves the cache split unknown, since nobody saw it."""
    if usage.prompt_tokens < IMPLICIT_CACHE_MIN_TOKENS:
        return usage
    return usage.model_copy(update={"cached_input_tokens": usage.prompt_tokens})


async def _answer_tokens(provider: Countable, unreported: Unreported) -> int:
    """The received answer counted on its own: one model message, with no system instruction and
    no tool declaration — those are the request's, and counted with it. A call's signature is the
    model's state, not output it is billed for, so it is left out."""
    parts: list[TextPart | ToolCallPart] = (
        [TextPart(text=unreported.answer)] if unreported.answer else []
    )
    parts.extend(call.model_copy(update={"signature": None}) for call in unreported.calls)
    if not parts:
        return 0
    reply = CanonicalRequest(
        model=unreported.sent.model,
        addressing=unreported.sent.addressing,
        messages=[CanonicalMessage(role=Role.MODEL, parts=parts)],
    )
    return await provider.count_tokens(reply, region=unreported.region)
