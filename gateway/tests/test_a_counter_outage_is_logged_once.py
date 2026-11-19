"""A counter-store outage is logged where it begins and where it ends — not by every request.

Without Redis configured, every budgeted request logged `budget_reservation_degraded` — a soak of
7 500 requests left 7 500 warnings — and every request booking a lifted budget or a pipeline step
another; during an outage the store's own warning and each lost correction's came on top. Running
without Redis is a choice the installation made, and `/readyz` already says it; a warning per
request is noise that hides the warnings that matter, and is shipped and paid for as telemetry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
import redis.exceptions
import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

import aira_common.counters as counters_module
import aira_gateway.budgets.ledger as ledger_module
import aira_gateway.budgets.service as service_module
from aira_common.counters import RETRY_AFTER_FAILURE_SECONDS, RedisRunner, build_runner
from aira_gateway.budgets.ledger import Amounts, BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class _Transport:
    """An in-process Redis behind a socket that times out on every call while ``down``."""

    def __init__(self) -> None:
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.down = False

    def register_script(self, script: str) -> Any:
        registered = self.redis.register_script(script)

        async def call(keys: Any, args: Any) -> Any:
            if self.down:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            return await registered(keys=keys, args=args)

        return call


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    async with build_sessionmaker(engine)() as session:
        for id_, period, enabled in ((1, "month", True), (2, "day", False)):
            session.add(
                BudgetRead(
                    id=id_,
                    use_case="uc",
                    scope="use_case",
                    subject="",
                    period=period,
                    limit_tokens=10_000_000,
                    enabled=enabled,
                )
            )
        await session.commit()
    yield build_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Every line the budgets and the counter store log. Fresh loggers, first used inside the
    capture: a module's own may be cached with the processors an earlier test configured."""
    with structlog.testing.capture_logs() as lines:
        for module, name in (
            (service_module, "aira_gateway.budgets"),
            (ledger_module, "aira_gateway.budgets"),
            (counters_module, "aira_common.counters"),
        ):
            monkeypatch.setattr(module, "_log", structlog.get_logger(name))
        yield lines


async def _serve(service: BudgetService, caller: str) -> None:
    reservation = await service.guard("uc", caller, NOW, estimated=Amounts(tokens=100, requests=1))
    await service.settle(reservation, 80, cost_nanos=5, now=NOW)
    await service.book_side_call("uc", caller, 10, cost_nanos=1, now=NOW)


async def test_without_a_counter_store_no_request_logs_anything(sessionmaker, logged) -> None:
    service = BudgetService(sessionmaker, ledger=BudgetLedger(build_runner("")))

    for n in range(20):
        await _serve(service, f"p{n}")

    assert logged == [], [line["event"] for line in logged]


async def test_an_outage_is_logged_where_it_begins_and_where_it_ends(sessionmaker, logged) -> None:
    now = [1000.0]
    runner = RedisRunner("redis://unused:6379/0", clock=lambda: now[0])
    transport = _Transport()
    runner._client = transport  # the transport only: `run` and its breaker are the real ones
    service = BudgetService(sessionmaker, ledger=BudgetLedger(runner))
    in_flight = await service.guard("uc", "ada", NOW, estimated=Amounts(tokens=100, requests=1))

    transport.down = True
    await service.settle(in_flight, 80, cost_nanos=5, now=NOW)  # its correction is held
    for n in range(10):
        now[0] += RETRY_AFTER_FAILURE_SECONDS + 1  # each request finds the store tried again
        await _serve(service, f"p{n}")
    transport.down = False
    now[0] += RETRY_AFTER_FAILURE_SECONDS + 1
    for n in range(3):
        await _serve(service, f"q{n}")

    events = [(line["log_level"], line["event"]) for line in logged]
    assert sorted(events) == sorted(
        [
            ("warning", "counters_unavailable"),
            ("warning", "budget_reservation_degraded"),
            ("info", "counters_available"),
            ("info", "budget_corrections_resent"),
            ("info", "budget_reservation_recovered"),
        ]
    ), events
