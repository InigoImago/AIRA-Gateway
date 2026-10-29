"""A stream that broke off is booked at what can be shown it cost, never as free (`FRD-408`).

Vertex's Gemini, and the open models behind the OpenAI dialect, report usage only in a stream's last
chunk. A stream that broke off before it was booked as having cost nothing, and its row was a
failure no report counted — invisible in every spend figure, while the vendor billed the prompt and
the answer as far as it went.

Who stopped decides what is known:

- **the connection, or this gateway's time limit** (`AIRA stopped waiting`) — the vendor did not
  stop and goes on answering, and billing, on its side. Counted at the vendor, as a stream its
  caller left is (`FRD-128` FR-6): the prompt and the answer received, a floor, and the row says
  so;
- **the vendor**, with an error of its own — it ended the answer itself, and nothing is booked;
- a report that came **before** the break is the vendor's running total — booked, and marked as the
  floor it is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

#: Both streamed verbs, as a client calls them.
SURFACES = {
    "gemini": (
        "/v1beta/models/brittle-1:streamGenerateContent?alt=sse",
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    ),
    "kira": (
        "/kira/api/external/streaming-chat",
        {"request": {"parts": [{"text": "hi"}]}, "model_id": 1},
    ),
}

#: Per million tokens, in nano-units: input 1 000, output 2 000 per token.
_PRICES = {
    "input_price_per_million_nanos": 1_000_000_000,
    "output_price_per_million_nanos": 2_000_000_000,
}

#: How the stream ends, by who stopped it.
TIMED_OUT = UpstreamError(
    "AIRA stopped waiting: Google Vertex AI sent nothing.", gateway_status=504
)
DROPPED = UpstreamError("Google Vertex AI broke off the connection (ReadError).")
VENDOR_ERROR = UpstreamError("Google Vertex AI returned 503.", 503)


class _Brittle:
    """A model that streams one chunk and then fails as it is told — and counts, as Vertex does."""

    is_test_double = True

    def __init__(self, failure: Exception, *, reported: CanonicalUsage | None = None) -> None:
        self.failure = failure
        self.reported = reported
        self.counted: list[CanonicalRequest] = []

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent")
        return [UpstreamModel("brittle-1", "1", verbs, provider="t", region="eu")]

    async def generate(self, request: CanonicalRequest) -> Any:
        raise NotImplementedError

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        yield CanonicalChunk(text_delta="par", usage=self.reported)
        raise self.failure

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]

    async def count_tokens(self, request: CanonicalRequest, *, region: str = "") -> int:
        self.counted.append(request)
        # The prompt first, then the answer received.
        return 40 if len(self.counted) == 1 else 2


def _rows(provider: _Brittle, surface: str, **settings: Any) -> list[tuple]:
    app = create_app(
        GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu", **settings)
    )
    app.state.providers = ProviderRegistry([provider])
    with TestClient(app) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(model="brittle-1", numeric_id=1, capabilities=["generate"], **_PRICES)
                )
                session.add(UseCaseRead(slug="demo", name="demo"))
                await session.commit()

        client.portal.call(seed)  # type: ignore[union-attr]
        path, body = SURFACES[surface]
        response = client.post(path, json=body, headers={"X-AIRA-Use-Case": "demo"})
        assert response.status_code == 200, "the status went out with the first chunk"

        async def read() -> list[tuple]:
            async with app.state.db_sessionmaker() as session:
                found = (await session.execute(select(RequestLog))).scalars()
                return [
                    (
                        row.status,
                        row.outcome,
                        row.prompt_tokens,
                        row.completion_tokens,
                        row.usage_basis,
                        row.cost_nanos,
                    )
                    for row in found
                ]

        return client.portal.call(read)  # type: ignore[union-attr]


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(("failure", "status"), [(TIMED_OUT, 504), (DROPPED, 502)])
def test_a_stream_the_connection_ended_is_counted_at_the_vendor(
    surface: str, failure: UpstreamError, status: int
) -> None:
    """The vendor did not stop: 40 prompt tokens and the 2 of the answer received are counted by
    its own tokenizer and booked at the ordinary rate (a prompt this short is never cached), and
    the row keeps the failure's status and says its usage is a count."""
    provider = _Brittle(failure)

    rows = _rows(provider, surface)

    assert rows == [(status, "upstream_error", 40, 2, "counted", 40 * 1_000 + 2 * 2_000)]
    assert len(provider.counted) == 2, "the request as sent, then the answer as received"


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_stream_the_vendor_ended_with_an_error_is_not_booked(surface: str) -> None:
    """The vendor ended the answer itself: nothing is sent to it a second time, nothing booked."""
    provider = _Brittle(VENDOR_ERROR)

    rows = _rows(provider, surface)

    assert rows == [(503, "upstream_error", None, None, None, None)]
    assert provider.counted == []


def test_counting_switched_off_sends_nothing_a_second_time() -> None:
    """`AIRA_COUNT_CANCELLED_STREAMS=false` covers this case as well: the text goes nowhere
    again, and the stream stays unknown."""
    provider = _Brittle(TIMED_OUT)

    rows = _rows(provider, "gemini", count_cancelled_streams=False)

    assert rows == [(504, "upstream_error", None, None, None, None)]
    assert provider.counted == []


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_report_before_the_break_is_booked_as_the_floor_it_is(surface: str) -> None:
    """A running total is what the answer had cost when it broke off, not what it cost: booked,
    and marked `partial` so every spend figure that includes it says it is a lower bound."""
    provider = _Brittle(TIMED_OUT, reported=CanonicalUsage(prompt_tokens=30, completion_tokens=1))

    rows = _rows(provider, surface)

    assert rows == [(504, "upstream_error", 30, 1, "partial", 30 * 1_000 + 1 * 2_000)]
    assert provider.counted == [], "the vendor's own figure needs no second look"
