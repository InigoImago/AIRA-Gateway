"""A streamed request is accounted for from the moment its chain is opened (`FRD-128`).

The pre-dispatch sequence reserves the request's allowance as its last step, and from there one
exit settles or releases it and writes the row (`accounting`). A stream opens its dispatch chain
before its response exists — the status waits up to `AIRA_STREAM_KEEPALIVE_SECONDS` for a first
chunk, and a thinking model says nothing for all of that — while a model is already being called.
Accounting that began only in the response's body left that window with two exits of its own: a
refusal, recorded at the surface, and an unexpected failure, which the application's last resort
answers with a `500` and no row. Two more fell through both:

- **a cancellation** — a stopping server cancels every request still running once its grace period
  is over — left the reservation in the shared counter for its whole expiry and wrote no row,
  though a model had been called;
- **a failure of this gateway's own** while the chain opened answered `500` with no row, where the
  same failure on `:generateContent` leaves a `500` / `internal_error` row.

Driven as a server drives the application (ASGI 2.3), with the real ledger scripts on an in-process
Redis, and the counter itself looked at.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import fakeredis.aioredis as fakeredis
import httpx
import pytest
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.budgets import ledger as ledger_scripts
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalChunk, CanonicalResponse
from aira_gateway.db.models import BudgetRead, ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel


class _Ledger:
    """The ledger's real scripts over an in-process Redis, remembering which ran."""

    def __init__(self) -> None:
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.calls: list[str] = []

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        self.calls.append("reserve" if script == ledger_scripts._RESERVE else "adjust")
        registered = self.client.register_script(script)
        return await registered(keys=list(keys), args=list(args))

    async def counters(self) -> list[dict[str, str]]:
        """Each budget counter's figures — not what the ledger keeps beside a counter."""
        figures = ("tokens", "requests", "cost")
        return [
            {
                name: value
                for name, value in (await self.client.hgetall(key)).items()
                if name in figures
            }
            for key in await self.client.keys("budget:*")
            if not key.endswith((":open", ":booked"))
        ]

    async def close(self) -> None:
        await self.client.aclose()


class _Thinking:
    """A model that reads its prompt — or thinks — for a long time before its first chunk."""

    is_test_double = True

    def __init__(self) -> None:
        self.called = asyncio.Event()

    def models(self) -> list[UpstreamModel]:
        verbs = ("generateContent", "streamGenerateContent")
        return [UpstreamModel("slow-1", "1", verbs, provider="t", region="eu")]

    async def generate(self, request: Any) -> CanonicalResponse:
        raise NotImplementedError

    async def stream_generate(self, request: Any) -> Any:
        self.called.set()  # the request is out: the vendor is reading the prompt
        await asyncio.Event().wait()
        yield CanonicalChunk(text_delta="never")  # pragma: no cover - never reached

    async def embed(self, request: Any) -> list[list[float]]:
        return [[0.0]]


class _Defective(_Thinking):
    """An adapter that trips over an answer it did not expect, on either verb."""

    async def generate(self, request: Any) -> CanonicalResponse:
        raise KeyError("candidates")

    async def stream_generate(self, request: Any) -> Any:
        raise KeyError("candidates")
        yield  # pragma: no cover - makes this an async generator


class _Overloaded(_Thinking):
    """A model whose provider refuses before it says anything: a refusal while still a status."""

    async def stream_generate(self, request: Any) -> Any:
        raise UpstreamError("overloaded", 503)
        yield  # pragma: no cover - makes this an async generator


#: Both streamed verbs, as a client calls them.
STREAMS = {
    "gemini": (
        "/v1beta/models/slow-1:streamGenerateContent",
        b"alt=sse",
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    ),
    "kira": (
        "/kira/api/external/streaming-chat",
        b"",
        {"request": {"parts": [{"text": "hi"}]}, "model_id": 1},
    ),
}

#: The same request, not streamed.
WHOLE = {
    "gemini": "/v1beta/models/slow-1:generateContent",
    "kira": "/kira/api/external/chat",
}


def _app(provider: _Thinking) -> tuple[Any, _Ledger]:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])
    ledger = _Ledger()
    app.state.budgets = BudgetService(app.state.db_sessionmaker, ledger=BudgetLedger(ledger))
    return app, ledger


async def _seed(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate"]))
        session.add(UseCaseRead(slug="demo", name="demo"))
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


async def _rows(app: Any) -> list[tuple[str, int, str]]:
    async with app.state.db_sessionmaker() as session:
        return [
            (row.operation, row.status, row.outcome)
            for row in (await session.execute(select(RequestLog))).scalars()
        ]


@pytest.mark.parametrize("surface", sorted(STREAMS))
async def test_a_stream_cancelled_while_its_chain_opens_is_released_and_recorded(
    surface: str,
) -> None:
    """The model has been asked and has not answered yet when the server cancels the request: the
    reservation goes back — resolved exactly once — and the request leaves its row, `499`."""
    path, query, body = STREAMS[surface]
    provider = _Thinking()
    app, ledger = _app(provider)
    async with app.router.lifespan_context(app):
        await _seed(app)
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": json.dumps(body).encode()}
            await asyncio.Event().wait()  # the caller is still there
            raise AssertionError("unreachable")  # pragma: no cover

        async def send(message: dict[str, Any]) -> None:
            pass

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
        served = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(provider.called.wait(), timeout=5)
        # Inside the wait for the first chunk: a stopping server cancels the request here.
        served.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await served
        counters = await ledger.counters()
        rows = await _rows(app)

    assert ledger.calls == ["reserve", "adjust"], "one reservation, resolved exactly once"
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
    assert [(status, outcome) for _, status, outcome in rows] == [(499, "client_gone")]


@pytest.mark.parametrize("surface", sorted(STREAMS))
async def test_a_defect_while_a_stream_opens_is_recorded_as_without_a_stream(surface: str) -> None:
    """An adapter that cannot read the answer it got fails the same way streamed or not: a `500`
    to the caller, and a `500` / `internal_error` row with the reservation given back."""
    path, _, body = STREAMS[surface]
    app, ledger = _app(_Defective())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        await _seed(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"x-aira-use-case": "demo"}
            whole = await client.post(WHOLE[surface], json=body, headers=headers)
            streamed = await client.post(f"{path}?alt=sse", json=body, headers=headers)
        counters = await ledger.counters()
        rows = await _rows(app)

    assert whole.status_code == streamed.status_code == 500
    assert sorted(status_and_outcome[1:] for status_and_outcome in rows) == [
        (500, "internal_error"),
        (500, "internal_error"),
    ]
    assert ledger.calls == ["reserve", "adjust", "reserve", "adjust"]
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]


@pytest.mark.parametrize("surface", sorted(STREAMS))
async def test_a_stream_refused_while_it_opens_leaves_one_row(surface: str) -> None:
    """A refusal while the status still waits is answered as a status and recorded once — by the
    surface's boundary, as every refusal is — with its reservation given back once."""
    path, _, body = STREAMS[surface]
    app, ledger = _app(_Overloaded())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        await _seed(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"{path}?alt=sse", json=body, headers={"x-aira-use-case": "demo"}
            )
        counters = await ledger.counters()
        rows = await _rows(app)

    assert response.status_code in {502, 503}
    assert [outcome for _, _, outcome in rows] == ["upstream_error"]
    assert ledger.calls == ["reserve", "adjust"]
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
