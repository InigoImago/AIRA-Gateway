"""Rate limiting and budget reservation against the live stack (FRD-405).

Two properties here cannot be demonstrated by the hermetic suites, and they are the two the
whole feature rests on:

1. **The limit is shared.** A unit test with a fake bucket proves the arithmetic; it cannot prove
   that two *separate processes* enforce one limit rather than one each. That is the property the
   load-balancer plan depends on, so it is tested against real Redis with two independent
   services — the same shape as two gateway replicas behind a load balancer.
2. **The reservation is atomic on real infrastructure.** ``fakeredis`` runs the same Lua, but the
   concurrency is simulated inside one event loop. Here the counters live in the real server.

Run with ``make test-integration`` while the stack is up.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.counters import build_runner
from aira_gateway.budgets.ledger import Amounts, BudgetLedger, Limits
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_sessionmaker
from aira_gateway.ratelimit.buckets import BucketRequest, RedisTokenBucket
from aira_gateway.ratelimit.service import RateLimitService

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

REDIS_URL = "redis://127.0.0.1:6379/0"


@pytest.fixture
async def runner():
    runner = build_runner(REDIS_URL)
    yield runner
    await runner.close()


async def _use_case(engine: AsyncEngine, slug: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name) VALUES (:slug, :slug)"), {"slug": slug}
        )


def _read_model_id() -> int:
    """An explicit id, because these read-model tables never generate their own.

    Every row arrives from Management carrying the id it has there, so the identity sequence is
    never advanced and cannot be relied on — inserting without an id collides with whatever
    Kafka delivered earlier.
    """
    return 900_000_000 + int(uuid.uuid4().int % 90_000_000)


async def _rate_limit(engine: AsyncEngine, slug: str, rpm: int, burst: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rate_limits (id, use_case, scope, subject, limit_rpm, burst, enabled)"
                " VALUES (:id, :slug, 'use_case', '', :rpm, :burst, true)"
            ),
            {"id": _read_model_id(), "slug": slug, "rpm": rpm, "burst": burst},
        )


async def _budget(engine: AsyncEngine, slug: str, **limits) -> int:
    budget_id = _read_model_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO budgets (id, use_case, scope, subject, period, limit_requests,"
                " limit_cost_nanos, enabled) VALUES (:id, :slug, 'use_case', '', 'month',"
                " :requests, :cost, true)"
            ),
            {
                "id": budget_id,
                "slug": slug,
                "requests": limits.get("limit_requests"),
                "cost": limits.get("limit_cost_nanos"),
            },
        )
        return budget_id


# ---- the property no unit test can show ----------------------------------------------------


async def test_two_gateway_instances_enforce_one_limit_not_one_each(engine, runner) -> None:
    """The reason for Redis. With per-process counters these two would allow twice the limit,
    and each would be individually "correct" — which is exactly how a load balancer turns a
    working limiter into a broken one."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _rate_limit(engine, slug, rpm=60, burst=4)
    sessionmaker = build_sessionmaker(engine)

    # Two services with separate caches and separate buckets objects — two processes, in effect.
    first = RateLimitService(sessionmaker, RedisTokenBucket(runner))
    second = RateLimitService(sessionmaker, RedisTokenBucket(build_runner(REDIS_URL)))

    allowed = 0
    for service in (first, second, first, second, first, second):
        try:
            await service.check(slug, "alice")
            allowed += 1
        except Exception:  # noqa: BLE001 - RateLimited; anything else fails the assertion below
            pass

    assert allowed == 4, "the burst of 4 was shared, not granted to each instance"


async def test_the_bucket_refills_on_the_servers_clock(engine, runner) -> None:
    """Refill uses Redis' own TIME, so instances that disagree about the wall clock still agree
    about the rate."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    bucket = RedisTokenBucket(runner)
    # 2 tokens, refilling at 10/second: exhaust, then wait for one to accrue.
    one = [BucketRequest(key=f"rl:itest:{slug}", capacity=2, refill_per_second=10.0)]

    assert (await bucket.take(one)).allowed
    assert (await bucket.take(one)).allowed
    refused = await bucket.take(one)
    assert refused.allowed is False
    assert refused.retry_after_seconds > 0

    await asyncio.sleep(0.3)
    assert (await bucket.take(one)).allowed is True


async def test_a_refused_scope_does_not_debit_the_other_on_real_redis(runner) -> None:
    """B1 against the real Lua: the multi-key script must decide over every bucket before
    debiting any of them, or one throttled member starves the whole use case."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    bucket = RedisTokenBucket(runner)
    wide = BucketRequest(key=f"rl:{{{slug}}}:uc", capacity=5, refill_per_second=0.01, label="uc")
    narrow = BucketRequest(
        key=f"rl:{{{slug}}}:member:alice", capacity=1, refill_per_second=0.01, label="member"
    )

    assert (await bucket.take([wide, narrow])).allowed  # alice spends her one
    for _ in range(4):
        decision = await bucket.take([wide, narrow])
        assert decision.allowed is False
        assert decision.refused is not None and decision.refused.label == "member"

    # The wide bucket had 5 and only one request ever passed, so four must remain for others.
    allowed = 0
    for _ in range(5):
        if (await bucket.take([wide])).allowed:
            allowed += 1
    assert allowed == 4


# ---- the budget race, on real infrastructure -----------------------------------------------


async def test_concurrent_requests_do_not_overshoot_a_request_budget(engine, runner) -> None:
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _budget(engine, slug, limit_requests=1)
    service = BudgetService(build_sessionmaker(engine), ledger=BudgetLedger(runner))

    async def attempt() -> bool:
        try:
            await service.guard(slug, "alice", estimated=Amounts(requests=1))
        except Exception:  # noqa: BLE001 - BudgetExceeded
            return False
        return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(25)))

    assert sum(outcomes) == 1


async def test_concurrent_requests_do_not_overshoot_a_cost_budget(engine, runner) -> None:
    """Since FRD-403 the limit is a sum of money, which is what makes the race an accounting
    defect rather than a cosmetic one."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _budget(engine, slug, limit_cost_nanos=1_000_000_000)  # 1.00
    service = BudgetService(build_sessionmaker(engine), ledger=BudgetLedger(runner))
    estimate = Amounts(tokens=1000, requests=1, cost_nanos=400_000_000)  # 0.40 each

    async def attempt() -> bool:
        try:
            await service.guard(slug, "alice", estimated=estimate)
        except Exception:  # noqa: BLE001 - BudgetExceeded
            return False
        return True

    outcomes = await asyncio.gather(*(attempt() for _ in range(20)))

    assert sum(outcomes) == 3, "0.40 × 3 fits under 1.00; the fourth must see the reservations"


async def test_a_released_reservation_returns_the_headroom(engine, runner) -> None:
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _budget(engine, slug, limit_requests=2)
    service = BudgetService(build_sessionmaker(engine), ledger=BudgetLedger(runner))

    for _ in range(10):
        reservation = await service.guard(slug, "alice", estimated=Amounts(requests=1))
        await service.release(reservation)

    # Ten failed attempts consumed nothing, so the budget is still open.
    assert (await service.guard(slug, "alice", estimated=Amounts(requests=1))).budgets != []


async def test_the_counter_is_seeded_from_postgres_when_redis_forgets(engine, runner) -> None:
    """Redis is deliberately not persisted (ADR-0008). A restart must not hand back a spent
    budget, which only holds because the counter reseeds from the system of record."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _budget(engine, slug, limit_requests=1)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO budget_usage (scope_key, period_key, tokens, requests, cost_nanos,"
                " unpriced_requests) VALUES (:key, to_char(now(), 'YYYY-MM'), 0, 1, 0, 0)"
            ),
            {"key": f"uc:{slug}"},
        )

    service = BudgetService(build_sessionmaker(engine), ledger=BudgetLedger(runner))

    with pytest.raises(Exception, match="budget exhausted"):
        await service.guard(slug, "alice", estimated=Amounts(requests=1))


async def test_the_ledger_reports_which_limit_refused(engine, runner) -> None:
    """The message a caller gets has to name the right limit, or an operator cannot tell a spend
    cap from a volume cap when the 429 arrives."""
    ledger = BudgetLedger(runner)
    key = f"itest-{uuid.uuid4().hex[:8]}"

    assert (
        await ledger.reserve(
            key,
            "2026-08",
            "month",
            limits=Limits(requests=1, cost_nanos=10),
            amounts=Amounts(requests=1, cost_nanos=1),
            seed=Amounts(),
        )
        is None
    )
    breached = await ledger.reserve(
        key,
        "2026-08",
        "month",
        limits=Limits(requests=1, cost_nanos=10),
        amounts=Amounts(requests=1, cost_nanos=1),
        seed=Amounts(),
    )
    assert breached == "requests"


# ---- the wired-up gateway -------------------------------------------------------------------


async def test_the_gateway_answers_429_with_retry_after_when_over_the_limit(engine) -> None:
    """End to end through the real service: a limit in the read-model, a burst of requests, and
    a response a client can actually act on."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    await _use_case(engine, slug)
    await _rate_limit(engine, slug, rpm=60, burst=2)

    from aira_common.apikeys import generate_api_key

    full_key, prefix, key_hash = generate_api_key()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, 'itest', :slug, 'itest', true)"
            ),
            {"id": f"{prefix}-itest", "prefix": prefix, "hash": key_hash, "slug": slug},
        )

    body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    headers = {"x-goog-api-key": full_key}
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        statuses = []
        for _ in range(6):
            response = await client.post(
                "/v1beta/models/mock-1:generateContent", json=body, headers=headers
            )
            statuses.append(response.status_code)
            if response.status_code == 429:
                assert response.headers.get("Retry-After"), (
                    "a 429 without Retry-After is a busy loop"
                )
                assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"

    assert statuses.count(200) == 2, f"burst of 2 expected, got {statuses}"
    assert 429 in statuses
