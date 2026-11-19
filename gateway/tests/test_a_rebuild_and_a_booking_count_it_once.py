"""A budget counter rebuilt while a booking is under way counts that booking exactly once.

A booking has two halves: it commits to Postgres, then corrects the shared counter. A rebuild has
two as well: it reads Postgres, then builds the counter in Redis. Nothing orders the halves of one
against the other, so a rebuild between a booking's commit and its correction counted the request
twice — the seed already held it and the correction added it again — and a seed read just before
the commit, built just after the correction had found no counter, counted it not at all. Either
lasted until the counter's next rebuild, and a correction held through an outage widens the window
from milliseconds to however long the store was away.

Postgres' own request count orders them: every booking counts a request, so the counter keeps the
count it was built on, a correction brings the row as its booking left it, and each side can tell
whether the other came first. Real Lua via fakeredis.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio
import redis.exceptions
from sqlalchemy.ext.asyncio import async_sessionmaker

import aira_gateway.budgets.service as service_module
from aira_common.counters import RETRY_AFTER_FAILURE_SECONDS, RedisRunner
from aira_gateway.budgets.ledger import Amounts, BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
COUNTER = "budget:uc:uc:2026-08"


class _Runner:
    """One gateway instance's way to the shared Redis, with nothing in between."""

    def __init__(self, server: fakeredis.FakeServer) -> None:
        self.redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    async def run(self, script: str, keys: Any, args: Any) -> Any:
        return await self.redis.register_script(script)(keys=list(keys), args=list(args))


class _Socket:
    """The transport under the real `RedisRunner`: the shared Redis, behind a socket whose calls
    in ``away`` time out before they reach it."""

    def __init__(self, server: fakeredis.FakeServer) -> None:
        self.redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        self.calls = 0
        self.away: set[int] = set()

    def register_script(self, script: str) -> Any:
        registered = self.redis.register_script(script)

        async def call(keys: Any, args: Any) -> Any:
            self.calls += 1
            if self.calls in self.away:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            return await registered(keys=keys, args=args)

        return call


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    async with build_sessionmaker(engine)() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="use_case",
                subject="",
                period="month",
                limit_tokens=1_000_000,
                enabled=True,
            )
        )
        await session.commit()
    yield build_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


async def _counted(runner: _Runner | _Socket) -> tuple[int, int]:
    counter = await runner.redis.hgetall(COUNTER)
    return int(counter["tokens"]), int(counter["requests"])


async def _postgres(sessionmaker: async_sessionmaker) -> tuple[int, int]:
    async with sessionmaker() as session:
        usage = await session.get(BudgetUsage, ("uc:uc", "2026-08"))
    assert usage is not None
    return usage.tokens, usage.requests


async def test_a_rebuild_between_a_bookings_commit_and_its_correction_counts_it_once(
    sessionmaker, server
) -> None:
    runner = _Runner(server)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1))
    record = service.record

    async def then_rebuilt(*args: Any, **kwargs: Any) -> Any:
        booked = await record(*args, **kwargs)  # committed: 4 000 tokens, one request
        await runner.redis.delete(COUNTER)  # the counter's lifetime ends here …
        await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))  # … rebuilt
        return booked

    service.record = then_rebuilt  # type: ignore[method-assign]
    await service.settle(first, 4_000, now=NOW)

    tokens, requests = await _postgres(sessionmaker)
    # What Postgres holds, and bob's request still in flight.
    assert await _counted(runner) == (tokens, requests + 1)


async def test_a_seed_read_before_a_booking_still_builds_on_it(
    sessionmaker, server, monkeypatch
) -> None:
    runner = _Runner(server)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    first = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1))
    await runner.redis.delete(COUNTER)  # the counter's lifetime is over
    read_usage = service_module.read_usage

    async def read_then_booked_elsewhere(session: Any, scope_key: str, period_key: str) -> Any:
        seed = await read_usage(session, scope_key, period_key)  # nothing booked yet
        # Before bob's reservation reaches Redis, alice's request ends on another worker: it
        # commits, and its correction finds no counter to move.
        monkeypatch.setattr(service_module, "read_usage", read_usage)
        await service.settle(first, 4_000, now=NOW)
        return seed

    monkeypatch.setattr(service_module, "read_usage", read_then_booked_elsewhere)
    await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))

    tokens, requests = await _postgres(sessionmaker)
    assert await _counted(runner) == (tokens, requests + 1)


async def test_a_pipeline_booking_beside_a_rebuild_counts_once(sessionmaker, server) -> None:
    """A step's call is booked without a reservation, the same two halves: committed, then added
    to the counter — which the rebuild in between had already built from it."""
    runner = _Runner(server)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    warm = await service.guard("uc", "alice", NOW, estimated=Amounts(tokens=100, requests=1))
    await service.settle(warm, 100, now=NOW)
    record = service.record

    async def then_rebuilt(*args: Any, **kwargs: Any) -> Any:
        booked = await record(*args, **kwargs)
        await runner.redis.delete(COUNTER)
        await service.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))
        return booked

    service.record = then_rebuilt  # type: ignore[method-assign]
    await service.book_side_call("uc", "alice", 50, now=NOW)

    tokens, requests = await _postgres(sessionmaker)
    assert await _counted(runner) == (tokens, requests + 1)


async def test_a_correction_held_through_a_rebuild_counts_the_request_once(
    sessionmaker, server
) -> None:
    """One instance's store is away for a moment as its request settles; another instance rebuilds
    the counter meanwhile, from a Postgres that already holds the request. The held correction,
    sent later, takes back the estimate and does not add the request a second time."""
    clock_now = [1000.0]
    runner = RedisRunner("redis://unused:6379/0", clock=lambda: clock_now[0])
    socket = _Socket(server)
    runner._client = socket  # the transport only: `run` and its breaker are the real ones
    here = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    elsewhere = BudgetService(sessionmaker, ledger=BudgetLedger(_Runner(server)))
    first = await here.guard("uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1))

    socket.away = {2}
    await here.settle(first, 4_000, now=NOW)  # booked in Postgres; the correction is held
    await socket.redis.delete(COUNTER)
    await elsewhere.guard("uc", "bob", NOW, estimated=Amounts(tokens=0, requests=1))
    clock_now[0] += RETRY_AFTER_FAILURE_SECONDS + 1
    await here.guard("uc", "carol", NOW, estimated=Amounts(tokens=0, requests=1))

    tokens, requests = await _postgres(sessionmaker)
    # What Postgres holds, and bob's and carol's requests still in flight.
    assert await _counted(socket) == (tokens, requests + 2)


async def test_a_booking_that_counts_no_request_is_corrected_in_full(sessionmaker, server) -> None:
    """The request count orders readings of Postgres only because a booking raises it. One that
    counts no request leaves it where it was, and must not be taken for one already counted."""
    runner = _Runner(server)
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    reservation = await service.guard(
        "uc", "alice", NOW, estimated=Amounts(tokens=10_000, requests=1)
    )

    await service.settle(reservation, 4_000, now=NOW, requests=0)

    assert await _counted(runner) == await _postgres(sessionmaker)
