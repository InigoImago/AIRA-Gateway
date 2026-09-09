"""A stream hands each piece over *before* the model produces the next one.

**This is the test that was missing, and its absence let a defect live behind three green
layers.** `/streaming-chat` called the non-streaming dispatch, waited for the whole answer and sent
one terminal event. Everything agreed that this was correct:

- the docstring described "exactly one `completed`" as the design;
- a hermetic test asserted exactly that, pinning it;
- a live probe counted the events, got a number, and the number looked plausible.

None of those can feel what a client feels. An SSE response that arrives entirely at the end is
indistinguishable from one that arrives progressively **unless you look at the order in which the
pieces move** — and nothing looked. So the property here is deliberately not "how many events" but
*"the surface let go of piece N before the model produced piece N+1"*. That is what streaming
**is**, and it is false for any implementation that assembles the answer first, however many events
it then emits.

**The harness had to change with the question, and that is the other half of the finding.** Written
first through `TestClient`, this case failed on *both* surfaces — including the one measured live
at a 4.3 s spread minutes earlier. `TestClient` collects the whole body before the caller sees a
line (the trap `CLAUDE.md` records for the disconnect tests), so through it every stream looks like
a block and the assertion is about the client, not the gateway. The app is therefore driven as the
ASGI application it is, observing each `http.response.body` as the app hands it over. That is the
moment the property is about; what a buffering client does afterwards is the client's business.

## Why there is no clock here any more (2026-09-09)

The first version paced the double with `asyncio.sleep` and asserted that the handovers spanned
*"more than half"* of the time the model took. It measured the right thing and it **decided by
stopwatch**, which is `LESSONS.md` §7's *a test whose verdict turns on how fast something answered
is measuring the machine*. It failed about one run in three in the full suite and passed six times
out of six in isolation — including under a saturated eight-core load, which is the measurement
that matters: the margin was not being eaten by a busy machine but by something that only happens
inside a long-lived process, and no threshold survives that. Raising it weakens the property;
lowering it keeps the coin toss.

The double now waits for a **handshake**. It produces a piece and then blocks until that piece has
left the application; only then does it produce the next. An implementation that streams walks
through that without noticing. One that assembles the answer first cannot — it has to read every
chunk before it sends anything, so it stalls on the first one and the run ends at `STALL_SECONDS`
with a message saying how far it got.

That is the **causal** version of the old assertion rather than a weaker one, and the only clock
left is a liveness bound that a slow machine can make *less* likely to fire, never more. Verified
the way this project verifies a guard, on both surfaces: `QA15` reintroduces the buffering
implementation on the KIRA side and is caught; the Gemini side was buffered by hand — draining
`model_call_chunks` into a list before the loop — and is caught too, in both cases with the stall
message rather than a number.

There is no live counterpart. This docstring named
`tests/integration/test_streams_actually_stream.py` until 2026-09-09 and no such file has ever
existed — an instruction with no destination (`LESSONS.md` §1), sitting in the paragraph that
explains what the hermetic layer cannot see. The live layer does drive the streaming path end to
end (`tests/integration/test_dev_round_evidence.py`, `test_local_model.py`); the **ordering** is
asserted here and nowhere else.
"""

from __future__ import annotations

import asyncio
import json
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

#: Enough pieces that "one at a time" is unmistakably what happened.
CHUNKS = 8
#: How long a **correct** stream may take before the run is called stalled. Nothing here sleeps, so
#: a healthy pass is a fifth of a second; this is a liveness bound rather than a measurement, and
#: its only job is to turn the deadlock a buffering surface produces into a legible failure.
#: Generous on purpose — a slow machine must not be able to make it fire.
STALL_SECONDS = 10.0
MODEL = "paced-1"
NUMERIC_ID = 7001


class _Paced:
    """A model that produces its next piece only once the last one has been handed over.

    The handshake **is** the assertion. :meth:`note_handover` is called from the ASGI ``send``
    below, when a ``data:`` body actually leaves the application; until then
    :meth:`stream_generate` does not run again. A surface that assembles the answer before sending
    anything therefore cannot get past its first piece, and says so by stalling.
    """

    is_test_double = True

    def __init__(self) -> None:
        # Constructed outside the loop that will await it, which has been safe since 3.10: an
        # `asyncio.Event` binds to no loop until it is first used.
        self._released = asyncio.Event()
        #: How many pieces have left the application so far.
        self.handovers = 0
        #: For each piece, how many handovers had happened when the model was asked for it.
        #: Reported in the failure message; a stream gives `[0, 1, 2, …]`.
        self.handovers_when_asked: list[int] = []

    def note_handover(self) -> None:
        self.handovers += 1
        self._released.set()

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                MODEL, "1", ("generateContent", "streamGenerateContent"), "acme", "acme", ""
            )
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        """The non-streaming call, answered instantly and on purpose.

        It used to sleep for as long as the streamed path took, so that a surface quietly calling
        *this* one could not pass by being faster than a timing assertion. There is no timing
        assertion left: a surface that calls this produces one terminal event, never releases the
        double for a second piece, and fails on the piece count with a message that says so.
        """
        return CanonicalResponse(
            model=MODEL,
            text="x" * CHUNKS,
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=CHUNKS),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        for index in range(CHUNKS):
            self.handovers_when_asked.append(self.handovers)
            # Cleared *before* the yield, so only a handover of this piece can release the next.
            self._released.clear()
            yield CanonicalChunk(text_delta=f"{index}")
            await self._released.wait()
        yield CanonicalChunk(
            text_delta="",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=CHUNKS),
            finish_reason="stop",
        )

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


@pytest.fixture
def paced() -> _Paced:
    return _Paced()


@pytest.fixture
def client(paced: _Paced) -> Iterator[TestClient]:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([paced])
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


async def _handed_over(app: Any, double: _Paced, url: str, body: dict[str, object]) -> int:
    """Drive the ASGI application and return how many ``data:`` pieces it let go of.

    Driving the application directly rather than through a client, because the property is *when
    the gateway lets go of a piece* — and every test client between here and there is free to
    collect the whole body first, which is exactly what the one used everywhere else does.
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

    status = 0

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body" and b"data: " in message.get("body", b""):
            double.note_handover()

    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=STALL_SECONDS)
    except TimeoutError:
        raise AssertionError(
            f"the application stalled after being asked for "
            f"{len(double.handovers_when_asked)} piece(s) and handing over {double.handovers}. "
            "It is holding the answer until the model has finished — which is what the model is "
            "waiting for it to stop doing. That is a request/response wearing an SSE costume, and "
            "counting the events cannot see it."
        ) from None
    assert status == 200, f"{url} answered {status}"
    return double.handovers


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
def test_each_piece_is_handed_over_before_the_next_is_produced(
    client: TestClient, paced: _Paced, surface: str, url: str, body: dict[str, object]
) -> None:
    """The whole point, and the thing counting events cannot see.

    An implementation that assembles the answer and then emits it must read every chunk before it
    sends one, so it never releases the double and the run ends at `STALL_SECONDS`. One that
    streams releases a piece per handover and finishes at once.

    **There is deliberately no assertion that `handovers_when_asked == [0, 1, 2, …]`.** The double
    enforces that itself — it cannot produce piece `n + 1` before a handover — so such an assertion
    could never fail, which is the guard-that-cannot-fail `LESSONS.md` §7 lists five instances of.
    The list is kept for the failure *message*, where it says how far a stalled surface got.

    Parametrised over both surfaces because "it streams" is a property of the product, not of one
    route — and asserting it for one of them is exactly how the other came to lack it.
    """
    app = client.app  # type: ignore[attr-defined]
    pieces = client.portal.call(_handed_over, app, paced, url, body)  # type: ignore[attr-defined]

    # Every piece was asked for, so the surface consumed the whole stream rather than stopping
    # early — and by the handshake above it can only have done that by handing each one over.
    assert len(paced.handovers_when_asked) == CHUNKS, (
        f"{surface}: the model was asked for {len(paced.handovers_when_asked)} of {CHUNKS} pieces"
    )
    # At least one `data:` per piece. Measured 2026-09-09: nine on both surfaces — eight updates
    # and one terminal event — so this floor has one to spare and does not pin the envelope.
    assert pieces >= CHUNKS, f"{surface}: {pieces} handover(s) for {CHUNKS} pieces"
