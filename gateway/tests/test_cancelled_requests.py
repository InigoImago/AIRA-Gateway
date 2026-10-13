"""A request the caller abandoned is still a request that happened (FRD-128).

Asked directly — *have all the paths been tested with a dropped connection?* — the answer was no.
Streaming had been: Gemini's by `aclose` and by a live client walking away, KIRA's by cancellation
during dispatch. **Every non-streaming path had not**, and all four of them lost the audit row.

The rule is `FRD-122`'s and it does not care how the request ended: the log records what was
**asked**, not only what was served. The upstream was called, tokens were spent, money was spent —
and a caller hanging up made all of that disappear from the record. `FRD-125b` already settled the
same argument for a pipeline step that cost money on a request that was then blocked.

Six paths, one table. This file is the acceptance test for consolidating the post-dispatch sequence
(`FRD-128`): before it, four of the six were wrong; the shape of the fix is that there is one place
left for them to be wrong in.
"""

import asyncio
import contextlib
import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.auth.attribution import Attribution
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


class Slow:
    """A model that takes its time, so a caller can go away while it is still answering."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                "slow-1",
                "1",
                ("generateContent", "embedContent"),
                provider="t",
                region="eu",
            )
        ]

    async def generate(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        return CanonicalResponse(
            model="slow-1",
            text="x",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        return [[0.0]]


def req(app, path, body):
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
        "client": ("127.0.0.1", 1),
        "app": app,
        "state": {},
        "path_params": {},
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(scope, receive)
    # Set by the auth dependency in a real call. Driving the handler directly is the only way to
    # cancel at the moment that matters — through `TestClient` the whole response is buffered
    # first, so the test would never reach the path it is about.
    request.state.attribution = Attribution(subject="demo", method="demo", use_case=None)
    return request


CASES = [
    (
        "gemini generateContent",
        "gemini",
        "slow-1:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    ),
    (
        "gemini embedContent",
        "gemini",
        "slow-1:embedContent",
        {"content": {"parts": [{"text": "hi"}]}},
    ),
    ("kira chat", "kira", None, {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}),
    ("kira embed", "kira", None, {"text": "hi", "model_id": 1}),
]


@pytest.mark.parametrize("label,surface,resource,body", CASES)
async def test_cancelled(label, surface, resource, body):
    provider = Slow()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate", "embed"]))
            await session.commit()
        from aira_gateway.auth.dependencies import _DEMO_PRINCIPAL

        if surface == "gemini":
            from aira_gateway.api.gemini.routes import generate

            r = req(app, f"/v1beta/models/{resource}", body)
            coro = generate(resource, r)
        elif "chat" in label:
            from aira_gateway.api.kira.routes import chat

            coro = chat(req(app, "/kira/api/external/chat", body), _DEMO_PRINCIPAL)
        else:
            from aira_gateway.api.kira.routes import embed

            coro = embed(req(app, "/kira/api/external/embed", body), _DEMO_PRINCIPAL)
        task = asyncio.create_task(coro)
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        provider.finish.set()
        await asyncio.sleep(0.15)
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())
    assert provider.calls == 1, f"{label}: upstream never reached"
    assert rows, f"{label}: CANCELLED REQUEST LEFT NO AUDIT ROW"


# == a disconnect as a server delivers it =========================================================
#
# Everything above cancels with one `task.cancel()`. A server does not: uvicorn speaks ASGI spec
# 2.3, so Starlette runs a streamed body inside an anyio task group and cancels that group's scope
# when `receive` reports `http.disconnect` — and anyio's cancellation is *level-triggered*: every
# await inside the scope is cancelled again, including one waiting on a shielded task. These drive
# the application exactly that way, with the real ledger scripts, and look at the counter itself.

import fakeredis.aioredis as fakeredis  # noqa: E402

from aira_gateway.budgets import ledger as ledger_scripts  # noqa: E402
from aira_gateway.budgets.ledger import BudgetLedger  # noqa: E402
from aira_gateway.budgets.service import BudgetService  # noqa: E402
from aira_gateway.core.canonical import CanonicalChunk  # noqa: E402
from aira_gateway.db.models import BudgetRead, BudgetUsage  # noqa: E402


class _CountingRunner:
    """The real scripts over an in-process Redis, remembering which ran — and optionally slow, as
    a counter store across a network is: a call that suspends is a call a cancellation can stop."""

    def __init__(self, delay: float = 0.0) -> None:
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.calls: list[str] = []
        self.delay = delay

    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append("reserve" if script == ledger_scripts._RESERVE else "adjust")
        registered = self.client.register_script(script)
        return await registered(keys=list(keys), args=list(args))

    async def counters(self) -> list[dict[str, str]]:
        return [await self.client.hgetall(key) for key in await self.client.keys("budget:*")]

    async def close(self) -> None:
        await self.client.aclose()


class _Streams:
    """A model that streams one chunk, then waits — so the caller can leave mid-answer."""

    is_test_double = True

    def __init__(self, *, usage_first: bool, finish_first: bool = False) -> None:
        self.usage_first = usage_first
        #: The first chunk is also the last word: usage and the reason it ended, together.
        self.finish_first = finish_first
        self.finish = asyncio.Event()

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                "slow-1",
                "1",
                ("generateContent", "streamGenerateContent"),
                provider="t",
                region="eu",
            )
        ]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        reported = CanonicalUsage(prompt_tokens=3, completion_tokens=2)
        yield CanonicalChunk(
            text_delta="do",
            usage=reported if self.usage_first else None,
            finish_reason="stop" if self.finish_first else None,
        )
        await self.finish.wait()
        yield CanonicalChunk(
            text_delta="ne",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=4),
            finish_reason="stop",
        )

    async def embed(self, request):  # noqa: ANN001, ANN201
        return [[0.0]]


class _CountedStreams(_Streams):
    """The same model, on a vendor that counts tokens at an endpoint of its own (`FRD-128` FR-6)."""

    def __init__(self, *totals: int, fail: Exception | None = None) -> None:
        super().__init__(usage_first=False)
        self.totals = list(totals)
        self.fail = fail
        self.asked: list[tuple[str, str]] = []

    async def count_tokens(self, request, *, region: str = "") -> int:  # noqa: ANN001
        # What was asked about, by role and text, and where.
        said = " | ".join(f"{message.role}:{message.text}" for message in request.messages)
        self.asked.append((said, region))
        if self.fail is not None:
            raise self.fail
        return self.totals.pop(0)


#: Per million tokens, in nano-units: input 1 000, cached input 100, output 2 000 per token.
_PRICES = {
    "input_price_per_million_nanos": 1_000_000_000,
    "cached_input_price_per_million_nanos": 100_000_000,
    "output_price_per_million_nanos": 2_000_000_000,
}


#: Both streamed verbs, as a client calls them.
STREAMS = [
    pytest.param(
        "/v1beta/models/slow-1:streamGenerateContent",
        b"alt=sse",
        {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1000},
        },
        id="gemini",
    ),
    pytest.param(
        "/kira/api/external/streaming-chat",
        b"",
        {"request": {"parts": [{"text": "hi"}]}, "model_id": 1},
        id="kira",
    ),
]


async def _disconnected(
    path: str,
    query: bytes,
    body: dict,
    *,
    usage_first: bool = True,
    delay: float = 0.0,
    gone_at: str = "after-frame",
    provider: _Streams | None = None,
    settings: dict | None = None,
    priced: bool = False,
) -> tuple[list[str], list[dict[str, str]], list[tuple], list[tuple]]:
    """Serve one stream over ASGI 2.3 and hang up: once the first frame is out (`after-frame`),
    while it is being sent (`during-frame`), or while the response is starting (`start`).

    Returns the ledger's calls, the counters, the audit rows and the booked usage — as they stand
    the moment the response is over, which is asserted to be how they stay. A row is `(status,
    outcome, prompt, completion, cached input, usage basis, cost)`.
    """
    provider = provider or _Streams(usage_first=usage_first)
    app = create_app(
        GatewaySettings(
            auth_required=False, log_queue_size=0, allowed_regions="eu", **(settings or {})
        )
    )
    app.state.providers = ProviderRegistry([provider])
    runner = _CountingRunner(delay)
    app.state.budgets = BudgetService(app.state.db_sessionmaker, ledger=BudgetLedger(runner))
    async with app.router.lifespan_context(app):
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="slow-1",
                    numeric_id=1,
                    capabilities=["generate"],
                    **(_PRICES if priced else {}),
                )
            )
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

        raw = json.dumps(body).encode()
        gone = asyncio.Event()
        delivered = False

        async def receive():  # noqa: ANN202
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):  # noqa: ANN001, ANN202
            starting = message["type"] == "http.response.start"
            framing = message["type"] == "http.response.body" and bool(message.get("body"))
            if (starting and gone_at == "start") or (framing and gone_at == "during-frame"):
                gone.set()
                # A transport draining under back-pressure: the send suspends, and the
                # cancellation lands inside it — before the body has run, or while it waits
                # between two frames.
                await asyncio.sleep(0.05)
            elif framing:
                gone.set()

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
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-aira-use-case", b"demo"),
                (b"host", b"test"),
            ],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }

        async def evidence() -> tuple[list[str], list[dict[str, str]], list[tuple], list[tuple]]:
            async with app.state.db_sessionmaker() as session:
                rows = [
                    (
                        row.status,
                        row.outcome,
                        row.prompt_tokens,
                        row.completion_tokens,
                        row.cached_input_tokens,
                        row.usage_basis,
                        row.cost_nanos,
                    )
                    for row in (await session.execute(select(RequestLog))).scalars()
                ]
                booked = [
                    (usage.tokens, usage.requests)
                    for usage in (await session.execute(select(BudgetUsage))).scalars()
                ]
            return list(runner.calls), await runner.counters(), rows, booked

        await app(scope, receive, send)
        when_over = await evidence()
        provider.finish.set()
        # Long enough for anything still running behind the response — a settle racing a release
        # was exactly that — to land before the evidence is read again.
        await asyncio.sleep(0.2)
        later = await evidence()
    assert later == when_over, "the request was still being accounted for after its response"
    return later


@pytest.mark.parametrize("gone_at", ["after-frame", "during-frame"])
@pytest.mark.parametrize(("path", "query", "body"), STREAMS)
async def test_a_stream_the_caller_leaves_is_settled_exactly_once(
    path: str, query: bytes, body: dict, gone_at: str
) -> None:
    """Usage arrived with the first chunk, so the stream is settled at what was spent — once. The
    shielded settle used to keep running after the cancellation had carried control out of the
    exit, `hold` released the reservation it had not yet resolved, and the settle then corrected a
    reservation that was already gone: the counter moved by `A − R` instead of `A`, and every
    abandoned stream lowered the shared figure that concurrent requests check against."""
    calls, counters, rows, booked = await _disconnected(path, query, body, gone_at=gone_at)

    assert calls == ["reserve", "adjust"], "one reservation, resolved exactly once"
    assert counters == [{"tokens": "5", "requests": "1", "cost": "0"}]
    assert booked == [(5, 1)]
    # Reported with the first chunk and before the end: the vendor's running total, a floor.
    assert rows == [(499, "client_gone", 3, 2, 0, "partial", None)]


@pytest.mark.parametrize(("path", "query", "body"), STREAMS)
async def test_a_stream_left_before_usage_gives_its_reservation_back_through_a_slow_store(
    path: str, query: bytes, body: dict
) -> None:
    """Nothing reported, so the reservation is released — and a release that has to wait for the
    counter store is not abandoned half-way by the cancellation that triggered it, or the estimate
    stays in the counter while every reservation refreshes its expiry."""
    calls, counters, rows, booked = await _disconnected(
        path, query, body, usage_first=False, delay=0.01
    )

    assert calls == ["reserve", "adjust"]
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
    assert booked == []
    assert rows == [(499, "client_gone", None, None, None, None, None)]


@pytest.mark.parametrize(("path", "query", "body"), STREAMS)
async def test_a_stream_cancelled_while_its_response_starts_is_recorded_and_released(
    path: str, query: bytes, body: dict
) -> None:
    """The upstream had already produced its first chunk when the cancellation landed inside
    `send(http.response.start)` — so the body never ran, and the accounting that lives in it never
    began: no row for a request that reached a model, and a reservation nobody gave back."""
    calls, counters, rows, booked = await _disconnected(path, query, body, gone_at="start")

    assert calls == ["reserve", "adjust"]
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
    assert booked == []
    assert rows == [(499, "client_gone", None, None, None, None, None)]


async def test_shielded_work_finishes_through_repeated_native_cancellations() -> None:
    """`asyncio.shield` lets the first cancellation through while the work goes on behind it; a
    second arrives at the next await. The exit has to finish what it began — the settle, the row —
    and only then let the cancellation through."""
    from aira_gateway.shielding import uninterrupted

    finished = asyncio.Event()

    async def work() -> str:
        await asyncio.sleep(0.05)
        finished.set()
        return "done"

    task = asyncio.create_task(uninterrupted(work()))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set(), "the cancellation was let through before the work had finished"


async def test_shielded_work_returns_what_it_produced() -> None:
    from aira_gateway.shielding import uninterrupted

    async def work() -> int:
        await asyncio.sleep(0)
        return 7

    assert await uninterrupted(work()) == 7


# == a stream left before its vendor reported usage is counted, never estimated (FRD-128 FR-6) ==


@pytest.mark.parametrize(("path", "query", "body"), STREAMS)
async def test_a_stream_left_before_usage_is_counted_at_the_vendor_and_booked(
    path: str, query: bytes, body: dict
) -> None:
    """The vendor reported nothing, so the request and the answer received are counted by its own
    tokenizer and that is booked — once, at a floor, and the row says how it was established. Its
    cache split and thinking were in a report that never came, so they stay unknown, not zero."""
    provider = _CountedStreams(40, 2)

    calls, counters, rows, booked = await _disconnected(path, query, body, provider=provider)

    assert calls == ["reserve", "adjust"], "one reservation, resolved exactly once"
    assert counters == [{"tokens": "42", "requests": "1", "cost": "0"}]
    assert booked == [(42, 1)]
    assert rows == [(499, "client_gone", 40, 2, None, "counted", None)]
    asked = [said for said, _ in provider.asked]
    assert asked[0].endswith("user:hi"), "the request as it was sent"
    assert asked[1] == "model:do", "the answer received — on its own, nothing of the request"


@pytest.mark.parametrize(
    ("prompt", "cost"),
    [
        # Below the shortest prompt Google caches: the ordinary rate is exact.
        (40, 40 * 1_000 + 2 * 2_000),
        # Long enough to have been cached: priced as cached, so never above the bill.
        (2_000, 2_000 * 100 + 2 * 2_000),
    ],
)
async def test_a_counted_stream_is_priced_at_its_floor(prompt: int, cost: int) -> None:
    path, query, body = STREAMS[0].values

    _, counters, rows, _ = await _disconnected(
        path, query, body, provider=_CountedStreams(prompt, 2), priced=True
    )

    assert rows == [(499, "client_gone", prompt, 2, None, "counted", cost)]
    assert counters[0]["cost"] == str(cost)


@pytest.mark.parametrize(("path", "query", "body"), STREAMS)
async def test_a_stream_left_while_its_response_starts_is_counted_from_its_prompt(
    path: str, query: bytes, body: dict
) -> None:
    """The first chunk had opened the stream, so the prompt was read; nothing of the answer was
    received, so only the prompt is counted."""
    provider = _CountedStreams(40)

    _, _, rows, booked = await _disconnected(path, query, body, provider=provider, gone_at="start")

    assert rows == [(499, "client_gone", 40, 0, None, "counted", None)]
    assert booked == [(40, 1)]
    assert len(provider.asked) == 1


async def test_counting_switched_off_sends_nothing_a_second_time() -> None:
    """`AIRA_COUNT_CANCELLED_STREAMS=false`: the text does not go to the vendor again, and the
    stream is what it was before — unknown, its reservation given back."""
    path, query, body = STREAMS[0].values
    provider = _CountedStreams(40, 2)

    _, counters, rows, booked = await _disconnected(
        path, query, body, provider=provider, settings={"count_cancelled_streams": False}
    )

    assert provider.asked == []
    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
    assert booked == []
    assert rows == [(499, "client_gone", None, None, None, None, None)]


async def test_a_count_that_fails_leaves_the_stream_unknown_and_its_reservation_returned() -> None:
    path, query, body = STREAMS[0].values

    _, counters, rows, booked = await _disconnected(
        path, query, body, provider=_CountedStreams(fail=ConnectionError("vendor away"))
    )

    assert counters == [{"tokens": "0", "requests": "0", "cost": "0"}]
    assert booked == []
    assert rows == [(499, "client_gone", None, None, None, None, None)]


async def test_a_report_that_came_with_the_end_is_the_vendors_last_word() -> None:
    """Usage arriving with the reason the answer ended is the vendor's final figure: booked as
    reported, and not marked as a floor however the caller left afterwards."""
    path, query, body = STREAMS[0].values

    _, _, rows, booked = await _disconnected(
        path, query, body, provider=_Streams(usage_first=True, finish_first=True)
    )

    assert rows == [(499, "client_gone", 3, 2, 0, None, None)]
    assert booked == [(5, 1)]
