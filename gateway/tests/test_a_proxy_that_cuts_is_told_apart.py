"""A connection a proxy cut is recorded so that the two kinds of cut read differently (`FRD-630`).

Between a caller and the gateway there is usually a proxy, and a proxy closes a connection for one
of two reasons: it has been **open too long** — a load balancer's response timeout counts from the
request, whatever flows — or it has been **silent too long** — nginx's `proxy_read_timeout` counts
from the last byte. Both reach the gateway as the same `http.disconnect`, and the row says `499`.
What tells them apart is two numbers the technical record keeps: how long the connection was open
when it closed, and how long it had been silent.

Everything below runs on real sockets: the gateway under uvicorn, a proxy of a few lines in front of
it that closes both sides, and a client that reads through it. A hand-driven ASGI `receive` would
decide *when* the gateway hears of the departure; here the operating system does, as in production.
Both limits are short, so a case takes about a second; the bounds allow for a slow machine and stay
far apart from each other, so neither cut can pass for the other.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import uvicorn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.base import watch_connections
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.base import UpstreamModel as Model

MODEL = "slow-1"
URL = f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse"
BODY = {"contents": [{"role": "user", "parts": [{"text": "read the repository"}]}]}

#: The proxy's limit in each case, in seconds: the real ones' minutes, scaled down.
LIMIT = 1.0
#: How far a recorded moment may lie from the proxy's: the request reaches the gateway a little
#: after the proxy accepted it, and the gateway hears of the close a little after the proxy closed.
EARLY, LATE = 0.15, 0.6
#: The keepalive interval while a stream is quiet — far below the limit, as fifteen seconds are far
#: below three minutes.
KEEPALIVE = 0.1
#: How long a case waits for its row before it fails saying so.
STALL_SECONDS = 15.0


class _Slow:
    """A model that says one word and then nothing more — as one thinking before a function call."""

    is_test_double = True

    def models(self) -> list[Model]:
        return [Model(MODEL, MODEL, ("generateContent", "streamGenerateContent"), "acme")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(model=MODEL, text="whole", usage=CanonicalUsage())

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        yield CanonicalChunk(text_delta="Let me look ")
        await asyncio.sleep(3600)

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _FixedStore:
    async def get(self, use_case: str | None) -> Pipeline:
        return Pipeline()


#: How a proxy decides to close: given when the connection opened and when it last carried a byte
#: from the gateway, the moment it closes.
Closes = Callable[[float, float], float]


def open_too_long(opened: float, last_byte: float) -> float:
    return opened + LIMIT


def silent_too_long(opened: float, last_byte: float) -> float:
    return last_byte + LIMIT


async def _proxy(gateway_port: int, closes: Closes) -> tuple[asyncio.Server, int]:
    """A proxy that relays both ways and closes both sides at the moment ``closes`` names."""

    async def handle(caller_r: asyncio.StreamReader, caller_w: asyncio.StreamWriter) -> None:
        opened = last_byte = time.monotonic()
        gateway_r, gateway_w = await asyncio.open_connection("127.0.0.1", gateway_port)

        async def relay(
            source: asyncio.StreamReader, sink: asyncio.StreamWriter, from_gateway: bool
        ) -> None:
            nonlocal last_byte
            while data := await source.read(65536):
                if from_gateway:
                    last_byte = time.monotonic()
                sink.write(data)
                await sink.drain()

        relays = [
            asyncio.create_task(relay(caller_r, gateway_w, False)),
            asyncio.create_task(relay(gateway_r, caller_w, True)),
        ]
        # Asked again whenever it passes: a byte from the gateway moves a silence limit's deadline.
        while (wait := closes(opened, last_byte) - time.monotonic()) > 0:
            done, _ = await asyncio.wait(relays, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
            if done:
                break
        for task in relays:
            task.cancel()
        for writer in (gateway_w, caller_w):
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _cut(database: Path, closes: Closes, **settings: Any) -> tuple[RequestLog, list[bytes]]:
    """One streamed request through a proxy that cuts it; the row it left, and what arrived.

    On a SQLite file, one connection per session: the in-memory database is one connection every
    session shares, and a case looking for its row while the gateway writes it would roll the
    gateway's write back with its own read.
    """
    engine = watch_connections(
        create_async_engine(f"sqlite+aiosqlite:///{database}", poolclass=NullPool)
    )
    with mock.patch("aira_gateway.app.build_engine", return_value=engine):
        app = create_app(
            GatewaySettings(
                auth_required=False,
                require_use_case=False,
                log_queue_size=0,
                **{"drain_left_streams_seconds": 0, **settings},
            )
        )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    serving = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn offers no event to wait on
        await asyncio.sleep(0.01)
    registry = ProviderRegistry([_Slow()])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore()
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=MODEL, approved=True, capabilities=["generate"]))
        await session.commit()

    proxy, port = await _proxy(server.servers[0].sockets[0].getsockname()[1], closes)
    received: list[bytes] = []
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            with contextlib.suppress(httpx.HTTPError):
                async with client.stream("POST", URL, json=BODY) as response:
                    async for piece in response.aiter_raw():
                        received.append(piece)
        row = await _wait_for(lambda: _row(app))
    finally:
        proxy.close()
        server.should_exit = True
        await serving
    return row, received


async def _row(app: Any) -> RequestLog | None:
    async with app.state.db_sessionmaker() as session:
        return (await session.execute(select(RequestLog))).scalars().first()


async def _wait_for(find: Callable[[], Awaitable[RequestLog | None]]) -> RequestLog:
    deadline = time.monotonic() + STALL_SECONDS
    while (row := await find()) is None:
        assert time.monotonic() < deadline, "the cut request left no row"
        await asyncio.sleep(0.05)
    return row


def _open_and_silent(row: RequestLog) -> tuple[float, float]:
    """The two numbers a technician reads off the record, in seconds."""
    assert row.technical is not None, "a cut request keeps its technical record"
    timeline = row.technical["timeline_ms"]
    left, last = timeline["caller_left"], timeline["last_byte_to_caller"]
    return left / 1000, (left - last) / 1000


async def test_a_proxy_that_limits_the_length_closes_a_connection_that_was_never_silent(
    tmp_path: Path,
) -> None:
    """A load balancer's response timeout: keepalives flow until the end, and it closes anyway.
    The record shows a connection open for the limit, with almost no silence before it closed.

    The model is read on after the cut, as an installation does to book the vendor's own figure:
    the moment recorded is the cut, not the end of that reading."""
    row, received = await _cut(
        tmp_path / "gateway.db",
        open_too_long,
        stream_keepalive_seconds=KEEPALIVE,
        drain_left_streams_seconds=LIMIT,
    )

    assert (row.status, row.outcome) == (499, "client_gone")
    assert received, "the caller received the status and keepalives before the cut"
    opened, silent = _open_and_silent(row)
    assert LIMIT - EARLY <= opened <= LIMIT + LATE, row.technical
    assert silent <= KEEPALIVE + LATE / 2, row.technical
    assert row.technical is not None and row.technical["phase"] == "mid_answer"
    assert row.technical["to_caller"]["keepalives"] >= LIMIT / KEEPALIVE / 2


async def test_a_proxy_that_limits_the_silence_closes_a_connection_that_had_gone_quiet(
    tmp_path: Path,
) -> None:
    """nginx's read timeout: no keepalive, the model fell quiet, and the proxy closed after the
    limit of silence. The record shows the silence before it closed as long as that limit."""
    row, received = await _cut(tmp_path / "gateway.db", silent_too_long, stream_keepalive_seconds=0)

    assert (row.status, row.outcome) == (499, "client_gone")
    assert received, "the model's first word reached the caller before the silence"
    opened, silent = _open_and_silent(row)
    assert LIMIT - EARLY <= silent <= LIMIT + LATE, row.technical
    assert opened >= silent
    assert row.technical is not None and row.technical["to_caller"]["keepalives"] == 0
