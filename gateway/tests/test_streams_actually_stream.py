"""A stream hands the answer over *while* it is produced — asserted against a clock.

**This is the test that was missing, and its absence let a defect live behind three green
layers.** `/streaming-chat` called the non-streaming dispatch, waited for the whole answer and sent
one terminal event. Everything agreed that this was correct:

- the docstring described "exactly one `completed`" as the design;
- a hermetic test asserted exactly that, pinning it;
- a live probe counted the events, got a number, and the number looked plausible.

None of those can feel what a client feels. An SSE response that arrives entirely at the end is
indistinguishable from one that arrives progressively **unless you look at when the pieces
arrive** — and nothing looked. So the property here is deliberately not "how many events" but
*"the first piece is out long before the last"*. That is what streaming **is**, and it is false for
any implementation that assembles the answer first, however many events it then emits.

**The harness had to change with the question, and that is the other half of the finding.** Written
first through `TestClient`, this case failed on *both* surfaces — including the one measured live
at a 4.3 s spread minutes earlier. `TestClient` collects the whole body before the caller sees a
line (the trap `CLAUDE.md` records for the disconnect tests), so through it every stream looks like
a block and the assertion is about the client, not the gateway. The app is therefore driven as the
ASGI application it is, stamping each `http.response.body` as the app hands it over. That is the
moment the property is about; what a buffering client does afterwards is the client's business.

The provider is a double that yields on a schedule, because the property is about the surface's
plumbing and must not depend on a real model's speed — a case that only passes on a slow model is
a case about the model. The live counterpart, against a model that never agreed to anything, is in
`tests/integration/test_streams_actually_stream.py`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

#: Long enough that "all at once" and "in pieces" cannot be confused, short enough for a suite.
GAP_SECONDS = 0.05
CHUNKS = 8
MODEL = "paced-1"
NUMERIC_ID = 7001


class _Paced:
    """A model that answers in pieces, on a clock."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                MODEL, "1", ("generateContent", "streamGenerateContent"), "acme", "acme", ""
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        # Deliberately as slow as the streaming path, so a surface that quietly calls *this* one
        # cannot pass by being faster than the assertion.
        await asyncio.sleep(CHUNKS * GAP_SECONDS)
        return CanonicalResponse(
            model=MODEL,
            text="x" * CHUNKS,
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=CHUNKS),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        for index in range(CHUNKS):
            await asyncio.sleep(GAP_SECONDS)
            yield CanonicalChunk(text_delta=f"{index}")
        yield CanonicalChunk(
            text_delta="",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=CHUNKS),
            finish_reason="stop",
        )

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([_Paced()])
    with TestClient(app) as running:

        async def _seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model=MODEL,
                        numeric_id=NUMERIC_ID,
                        capabilities=["generate"],
                        approved=True,
                    )
                )
                await session.commit()

        running.portal.call(_seed)  # type: ignore[attr-defined]
        yield running


async def _handover_times(app: Any, url: str, body: dict[str, object]) -> list[float]:
    """When the application handed each `data:` piece to the server, relative to the request.

    Driving the ASGI application directly rather than through a client, because the property is
    *when the gateway lets go of a piece* — and every test client between here and there is free
    to collect the whole body first, which is exactly what the one used everywhere else does.
    """
    path, _, query = url.partition("?")
    payload = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 32768),
        "server": ("testserver", 80),
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            # Never a disconnect: a stream cut short here would be testing `FRD-128`'s
            # `client_gone` path, which has its own cases.
            await asyncio.sleep(3600)
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    started = time.monotonic()
    stamps: list[float] = []
    status = 0

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body" and b"data: " in message.get("body", b""):
            stamps.append(time.monotonic() - started)

    await app(scope, receive, send)
    assert status == 200, f"{url} answered {status}"
    return stamps


@pytest.mark.parametrize(
    ("surface", "url", "body"),
    [
        pytest.param(
            "kira",
            "/kira/api/external/streaming-chat",
            {"request": {"parts": [{"text": "hallo"}]}, "model_id": NUMERIC_ID},
            id="kira",
        ),
        pytest.param(
            "gemini",
            f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse",
            {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]},
            id="gemini",
        ),
    ],
)
def test_the_first_piece_is_handed_over_long_before_the_last(
    client: TestClient, surface: str, url: str, body: dict[str, object]
) -> None:
    """The whole point, and the thing counting events cannot see.

    An implementation that assembles the answer and then emits it hands everything over at
    essentially one instant; one that streams spreads the handovers across the time the model took.
    The double takes `CHUNKS * GAP_SECONDS`, so a real stream must span most of that and a fake one
    spans nothing.

    Parametrised over both surfaces because "it streams" is a property of the product, not of one
    route — and asserting it for one of them is exactly how the other came to lack it.
    """
    app = client.app  # type: ignore[attr-defined]
    arrivals = client.portal.call(_handover_times, app, url, body)  # type: ignore[attr-defined]

    assert len(arrivals) >= 2, f"{surface}: nothing to compare — {len(arrivals)} piece(s)"
    span = arrivals[-1] - arrivals[0]
    produced = CHUNKS * GAP_SECONDS

    assert span > produced / 2, (
        f"{surface}: every piece was handed over within {span:.3f}s of the first, while the model "
        f"took {produced:.3f}s to produce them — the answer was assembled first and sent as a "
        "block. That is a request/response wearing an SSE costume, and counting the events cannot "
        "see it."
    )
    # And the first piece is out early, which is what a reader actually experiences.
    assert arrivals[0] < produced, f"{surface}: nothing left the gateway until the answer was done"
