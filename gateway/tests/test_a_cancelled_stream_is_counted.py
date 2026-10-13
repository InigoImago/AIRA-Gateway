"""A stream its caller left before the vendor said what it used is counted — never estimated.

`FRD-128` FR-6. Vertex's Gemini reports usage only in a stream's last chunk, so a stream left before
it was booked as having cost nothing, though the prompt had been read and an answer begun. What can
be **known** is counted by the vendor's own tokenizer at its free endpoint: the request as sent and
the answer as received. These are the unit's properties; `test_cancelled_requests.py` holds the
same through the app, the booking and the row.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from aira_gateway.api.serving import counting
from aira_gateway.api.serving.counting import (
    IMPLICIT_CACHE_MIN_TOKENS,
    Unreported,
    count_unreported,
    floor_priced,
)
from aira_gateway.api.serving.streaming import StreamAccounting
from aira_gateway.audit import AuditTrail
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
    TextPart,
    ToolCallPart,
    ToolDeclaration,
)
from aira_gateway.pipeline.dispatch import Streaming
from aira_gateway.upstreams.base import UpstreamError


class _Counter:
    """A vendor that counts: each request it is asked about is kept, and answered in turn."""

    def __init__(self, *totals: int, fail: Exception | None = None, delay: float = 0.0) -> None:
        self.totals = list(totals)
        self.asked: list[tuple[CanonicalRequest, str]] = []
        self.fail = fail
        self.delay = delay

    async def count_tokens(self, request: CanonicalRequest, *, region: str = "") -> int:
        self.asked.append((request, region))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return self.totals.pop(0)


class _Silent:
    """A vendor with no counting endpoint: an OpenAI-compatible server, say."""


def _sent() -> CanonicalRequest:
    return CanonicalRequest(
        model="g",
        addressing={"regions": ["europe-west1", "europe-west4"]},
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.USER, text="weather in Berlin?"),
        ],
        tools=(ToolDeclaration(name="get_weather"),),
    )


def _left(provider: Any, *, answer: str = "It is", calls: tuple[ToolCallPart, ...] = ()) -> Any:
    return Unreported(
        provider=provider, sent=_sent(), answer=answer, calls=calls, region="europe-west4"
    )


async def test_the_request_and_the_received_answer_are_counted_where_they_went() -> None:
    counter = _Counter(34, 5)
    call = ToolCallPart(
        id="c1", name="get_weather", arguments={"city": "Berlin"}, signature="c2lnbmF0dXJl"
    )

    usage = await count_unreported(_left(counter, calls=(call,)))

    assert usage == CanonicalUsage(prompt_tokens=34, completion_tokens=5)
    (prompt, prompt_region), (answer, answer_region) = counter.asked
    assert prompt == _sent(), "the request exactly as it was sent"
    assert prompt_region == answer_region == "europe-west4", "counted where it went"
    # The answer on its own: the system instruction and the tools are the request's.
    assert [message.role for message in answer.messages] == [Role.MODEL]
    assert answer.tools == ()
    text, counted_call = answer.messages[0].parts
    assert isinstance(text, TextPart) and text.text == "It is"
    assert isinstance(counted_call, ToolCallPart)
    assert counted_call.signature is None, "a signature is the model's state, not billed output"


async def test_an_answer_never_received_is_counted_as_none_without_asking() -> None:
    counter = _Counter(34)

    usage = await count_unreported(_left(counter, answer=""))

    assert usage == CanonicalUsage(prompt_tokens=34, completion_tokens=0)
    assert len(counter.asked) == 1


async def test_a_vendor_that_cannot_count_leaves_the_usage_unknown() -> None:
    assert await count_unreported(_left(_Silent())) is None


@pytest.mark.parametrize("fail", [UpstreamError("refused", 403), ValueError("not a number")])
async def test_a_count_that_fails_leaves_the_usage_unknown(fail: Exception) -> None:
    assert await count_unreported(_left(_Counter(fail=fail))) is None


async def test_a_count_that_does_not_answer_is_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit is shielded and the caller gone; without a bound, a hanging count would hold the
    request's task — and its booking and row — for as long as the vendor liked."""
    monkeypatch.setattr(counting, "COUNT_TIMEOUT_SECONDS", 0.01)

    assert await count_unreported(_left(_Counter(34, delay=1.0))) is None


def test_a_prompt_too_short_to_cache_is_priced_at_the_ordinary_rate() -> None:
    usage = CanonicalUsage(prompt_tokens=IMPLICIT_CACHE_MIN_TOKENS - 1, completion_tokens=3)

    assert floor_priced(usage) == usage


def test_a_prompt_long_enough_to_cache_is_priced_as_cached_so_never_above_the_bill() -> None:
    usage = CanonicalUsage(prompt_tokens=IMPLICIT_CACHE_MIN_TOKENS, completion_tokens=3)

    priced = floor_priced(usage)

    assert priced.cached_input_tokens == IMPLICIT_CACHE_MIN_TOKENS
    assert priced.uncached_input_tokens == 0
    assert usage.cached_input_tokens == 0, "only the price takes the floor, never the counts"


async def _no_more_chunks() -> AsyncIterator[CanonicalChunk]:
    return
    yield  # pragma: no cover - makes this an async generator


def test_a_notice_put_before_the_answer_is_not_counted_as_the_models_output() -> None:
    """A routing step's notice leads the first text the caller sees (`FRD-309`). It is this
    gateway's sentence, not the vendor's output — counted as the answer, it would be booked as
    tokens the vendor never produced."""
    stream = StreamAccounting(
        None,  # type: ignore[arg-type]  # the request is only held, never read, by these methods
        AuditTrail(operation="streamGenerateContent", api="gemini"),
        # Only what the stream reads of the prepared request: its notices and its schema.
        SimpleNamespace(canonical=None, notices=("Answered by g.",)),  # type: ignore[arg-type]
        Streaming(
            model="g",
            candidate_index=0,
            chunks=_no_more_chunks(),
            provider=_Counter(),  # type: ignore[arg-type]
            sent=_sent(),
            region="europe-west4",
        ),
        api="gemini",
        operation="streamGenerateContent",
        started=0.0,
    )

    delivered = stream.take(CanonicalChunk(text_delta="Hello"))
    left = stream._unreported()  # noqa: SLF001 - what the exit hands to the count

    assert delivered.text_delta.startswith("Answered by g."), "the caller is told"
    assert left is not None
    assert left.answer == "Hello", "the vendor is asked about its own words only"
    assert left.region == "europe-west4"
