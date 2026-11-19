"""A stopping server waits for the requests it cut short before it closes what they need.

A server that is told to stop waits for its running requests for a grace period — the image passes
uvicorn `--timeout-graceful-shutdown` — then cancels every one still running and at once runs the
application's shutdown. A model's answer usually takes longer than that grace period, so on every
deploy there are streams still going. Each cancelled request still has its exit to run: read no
further, count what its vendor has not reported, settle, hand its row to the writer. The shutdown
closed the writer, the counters, the vendors' clients and the database right away, because it
waited only for requests that were reading on after their caller left — and a request cut by the
server registered as one only a step later, when its exit had already begun. Rows reached a writer
that had stopped, and a stream whose vendor reports usage only at its end was counted against a
closed client: booked as unknown, which is free.

So every request is counted from the moment it is accounted for until its row is handed over, and
the shutdown waits for those — briefly, inside the time a container is given to stop — before it
closes anything. Driven as uvicorn drives it: the request's task cancelled, then the lifespan's
shutdown, one step behind.

The database engine's `dispose` is recorded rather than run: the hermetic suite's in-memory SQLite
is one connection every session shares, and disposing of it under a connection in use raises —
which says nothing about a server's database, and would hide what is checked here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.app import create_app
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalChunk, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import BudgetRead, ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

STREAM = "/v1beta/models/slow-1:streamGenerateContent"
WHOLE = "/v1beta/models/slow-1:generateContent"
BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

#: How long a call to the shared counter store takes here: a store across a network suspends, and
#: an exit that suspends is one a shutdown that does not wait overtakes.
STORE_SECONDS = 0.05


class _Answering:
    """A model that answers one chunk and then goes on — or, not streamed, has not answered yet —
    when the server stops."""

    is_test_double = True

    def __init__(self, *, usage_first: bool = True) -> None:
        self.usage_first = usage_first
        self.asked = asyncio.Event()

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent")
        return [UpstreamModel("slow-1", "1", verbs, provider="t", region="eu")]

    async def generate(self, request: Any) -> CanonicalResponse:
        self.asked.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream_generate(self, request: Any) -> Any:
        yield CanonicalChunk(
            text_delta="do",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2)
            if self.usage_first
            else None,
        )
        await asyncio.Event().wait()  # the rest of a long answer
        yield CanonicalChunk(text_delta="ne")  # pragma: no cover - never reached

    async def embed(self, request: Any) -> list[list[float]]:
        return [[0.0]]


class _CountedAtTheVendor(_Answering):
    """The same model on a vendor that reports usage only at the end and counts at an endpoint of
    its own (Vertex's Gemini). Its client is closed at shutdown, and a closed client refuses to
    send, as `httpx.AsyncClient` does."""

    def __init__(self) -> None:
        super().__init__(usage_first=False)
        self.closed = False
        self.counted: list[str] = []

    async def count_tokens(self, request: Any, *, region: str = "") -> int:
        if self.closed:
            self.counted.append("refused: the client is closed")
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        await asyncio.sleep(0.005)
        self.counted.append("counted")
        return 7

    async def aclose(self) -> None:
        self.closed = True


class _SlowStore:
    """The ledger's scripts on an in-process Redis, each call taking `STORE_SECONDS`."""

    def __init__(self) -> None:
        self.client = fakeredis.FakeRedis(decode_responses=True)

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        await asyncio.sleep(STORE_SECONDS)
        return await self.client.register_script(script)(keys=list(keys), args=list(args))

    async def close(self) -> None:
        await self.client.aclose()


def _request(path: str, query: bytes, first_frame: asyncio.Event) -> dict[str, Any]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"content-type", b"application/json"), (b"x-aira-use-case", b"demo")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": json.dumps(BODY).encode()}
        await asyncio.Event().wait()  # the caller stays connected
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_frame.set()

    return {"scope": scope, "receive": receive, "send": send}


async def _stopped_while_serving(
    provider: _Answering,
    monkeypatch: pytest.MonkeyPatch,
    *,
    streamed: bool = True,
    budgeted: bool = False,
) -> tuple[bool, list[str], list[tuple[Any, ...]]]:
    """Serve one request, stop the server the way uvicorn does once its grace period is over, and
    return whether the request was over when the shutdown returned, the order things happened in,
    and the request's row."""
    events: list[str] = []

    async def recorded_dispose(self: AsyncEngine, close: bool = True) -> None:
        events.append("engine_disposed")

    monkeypatch.setattr(AsyncEngine, "dispose", recorded_dispose)
    app = create_app(
        GatewaySettings(auth_required=False, allowed_regions="eu", drain_left_streams_seconds=600)
    )
    app.state.providers = ProviderRegistry([provider])
    if budgeted:
        app.state.budgets = BudgetService(
            app.state.db_sessionmaker, ledger=BudgetLedger(_SlowStore())
        )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate"]))
        session.add(UseCaseRead(slug="demo", name="demo"))
        if budgeted:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="demo",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_tokens=10_000_000,
                    enabled=True,
                )
            )
        await session.commit()
    writer = app.state.log_writer
    submit, stop = writer.submit, writer.stop

    async def handed(entry: Any) -> None:
        events.append(f"row_handed:{entry.status}")
        await submit(entry)

    async def stopped() -> None:
        await stop()
        events.append("writer_stopped")

    writer.submit, writer.stop = handed, stopped

    going = asyncio.Event()
    call = _request(*((STREAM, b"alt=sse") if streamed else (WHOLE, b"")), going)
    served = asyncio.create_task(app(call["scope"], call["receive"], call["send"]))
    # The stream's first frame is out, or the model has been asked and has not answered yet.
    await asyncio.wait_for((going if streamed else provider.asked).wait(), timeout=5)

    # uvicorn, once its grace period is over: cancel the request, then shut the application down.
    served.cancel()
    await asyncio.sleep(0)
    await asyncio.wait_for(lifespan.__aexit__(None, None, None), timeout=10)
    over = served.done()

    await asyncio.gather(served, return_exceptions=True)
    async with app.state.db_sessionmaker() as session:
        rows = [
            (row.status, row.outcome, row.prompt_tokens, row.completion_tokens, row.usage_basis)
            for row in (await session.execute(select(RequestLog))).scalars()
        ]
    return over, events, rows


async def test_a_stream_cut_by_the_stop_hands_its_row_over_before_the_writer_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    over, events, rows = await _stopped_while_serving(_Answering(), monkeypatch)

    assert events.index("row_handed:499") < events.index("writer_stopped"), events
    assert over, f"the shutdown returned while the stream it cut was still accounting: {events}"
    # What the vendor had reported when the server stopped: a running total, a floor.
    assert rows == [(499, "client_gone", 3, 2, "partial")]


async def test_a_stream_cut_by_the_stop_is_counted_while_its_vendors_client_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing reported when the server stopped: the request and the answer received are counted
    at the vendor — before the shutdown closes the vendor's client — and booked at that floor,
    rather than as unknown, which is free."""
    provider = _CountedAtTheVendor()

    _, _, rows = await _stopped_while_serving(provider, monkeypatch)

    assert provider.counted == ["counted", "counted"]
    assert rows == [(499, "client_gone", 7, 7, "counted")]


async def test_a_request_cut_before_its_answer_hands_its_row_over_before_the_writer_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not only streams: a request the server cut while its model was still answering gives its
    reservation back through the counter store and writes its row, and the shutdown waits for both
    — not only for the requests that were reading on."""
    over, events, rows = await _stopped_while_serving(
        _Answering(), monkeypatch, streamed=False, budgeted=True
    )

    assert events.index("row_handed:499") < events.index("writer_stopped"), events
    assert over, f"the shutdown returned while the request it cut was still accounting: {events}"
    assert rows == [(499, "client_gone", None, None, None)]
