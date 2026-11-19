"""A server error costs the request it answers, and no other.

An unhandled exception is answered with a `500` and then goes on to the server, which closes the
connection — the answer has gone out by then. A client that was not told the connection ends puts
it back into its pool, as every SDK and every proxy's pool of connections to this gateway does, and
sends its next request on it: that request meets a closed connection and fails without ever reaching
the gateway. One bug then costs two callers, the second of whom did nothing wrong and sees a
connection reset rather than an answer. So the `500` says `Connection: close`, and the client opens
a new connection for what it sends next.

Run on real sockets: uvicorn serving the application, an httpx pool of one connection in front of
it. Whether the next request meets the close is a matter of timing — how busy the event loop is, how
long a traceback takes to write — so here the close comes at the worst moment every time rather than
now and then: just as the client's next request arrives (`_ClosesAsTheNextRequestArrives`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import uvicorn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from starlette.types import ASGIApp, Receive, Scope, Send

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

#: How long the server waits for the client's next request before it closes the connection anyway.
NEXT_REQUEST_WITHIN = 5.0


class _Upstream:
    """Two models behind one adapter: one whose answer the adapter cannot read, one that answers."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent",)
        return [UpstreamModel(model, model, verbs) for model in ("broken-1", "fine-1")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        if request.model == "broken-1":
            raise KeyError("candidates")  # a defect in reading the vendor's answer
        return CanonicalResponse(
            model="fine-1", text="hello", usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _ClosesAsTheNextRequestArrives:
    """The server's side of an unhandled error at its worst moment: the exception reaches the server
    — which closes the connection when it does — only once the client's next request has arrived."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.arrived = asyncio.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            self.arrived.set()
        try:
            await self.app(scope, receive, send)
        except Exception:
            # The answer has gone out; nothing was awaited since, so nothing has arrived since.
            self.arrived.clear()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(NEXT_REQUEST_WITHIN):
                    await self.arrived.wait()
            raise


async def test_the_request_after_a_server_error_reaches_the_gateway(tmp_path: Path) -> None:
    database = tmp_path / "gateway.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}", poolclass=NullPool)
    with mock.patch("aira_gateway.app.build_engine", return_value=engine):
        app = create_app(
            GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0)
        )
    served = _ClosesAsTheNextRequestArrives(app)
    server = uvicorn.Server(uvicorn.Config(served, host="127.0.0.1", port=0, log_level="critical"))
    serving = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn offers no event to wait on
        await asyncio.sleep(0.01)
    app.state.providers = ProviderRegistry([_Upstream()])
    async with app.state.db_sessionmaker() as session:
        for model in ("broken-1", "fine-1"):
            session.add(ModelRead(model=model, approved=True, capabilities=["generate"]))
        await session.commit()

    port = server.servers[0].sockets[0].getsockname()[1]
    # One connection, kept alive: every request after the first is offered the same one.
    pool = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    answered: list[Any] = []
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}/v1beta/models", limits=pool, timeout=10
        ) as client:
            for model in ("broken-1", "fine-1", "fine-1", "fine-1"):
                try:
                    response = await client.post(f"/{model}:generateContent", json=BODY)
                except httpx.TransportError as exc:
                    answered.append(type(exc).__name__)
                else:
                    answered.append(response.status_code)
        async with app.state.db_sessionmaker() as session:
            rows = [
                (row.model, row.status, row.outcome)
                for row in (await session.execute(select(RequestLog))).scalars()
            ]
    finally:
        server.should_exit = True
        await serving

    assert answered == [500, 200, 200, 200], f"a server error cost another request: {answered}"
    # The error is recorded as this gateway's own, and every request after it reached the gateway.
    assert sorted(rows) == [
        ("broken-1", 500, "internal_error"),
        ("fine-1", 200, "served"),
        ("fine-1", 200, "served"),
        ("fine-1", 200, "served"),
    ]
