"""Request-rate limiting (FRD-405).

The interesting properties are all about *time* and *who shares a bucket with whom*, so the
clock is injected rather than slept through: a limiter exercised only at a single instant is
barely exercised at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.counters import CountersUnavailable
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import Base
from aira_gateway.db.models import RateLimitRead
from aira_gateway.ratelimit.buckets import (
    BucketDecision,
    FallbackTokenBucket,
    InMemoryTokenBucket,
    RedisTokenBucket,
)
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.ratelimit.service import RateLimitService


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _limit(sessionmaker, **fields) -> None:
    defaults = {
        "id": fields.pop("id", 1),
        "use_case": "uc",
        "scope": "use_case",
        "subject": "",
        "limit_rpm": 60,
        "burst": 0,
        "enabled": True,
    }
    defaults.update(fields)
    async with sessionmaker() as session:
        session.add(RateLimitRead(**defaults))
        await session.commit()


# ---- the bucket itself -------------------------------------------------------------------


async def test_a_burst_is_allowed_up_to_the_capacity() -> None:
    bucket = InMemoryTokenBucket(FakeClock())
    allowed = [(await bucket.take("k", 5, 1.0)).allowed for _ in range(5)]
    assert allowed == [True] * 5


async def test_the_request_after_the_burst_is_refused() -> None:
    bucket = InMemoryTokenBucket(FakeClock())
    for _ in range(5):
        await bucket.take("k", 5, 1.0)
    assert (await bucket.take("k", 5, 1.0)).allowed is False


async def test_the_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock)
    for _ in range(5):
        await bucket.take("k", 5, 1.0)
    assert (await bucket.take("k", 5, 1.0)).allowed is False

    clock.advance(2.0)  # one token per second
    assert (await bucket.take("k", 5, 1.0)).allowed is True
    assert (await bucket.take("k", 5, 1.0)).allowed is True
    assert (await bucket.take("k", 5, 1.0)).allowed is False


async def test_the_bucket_never_refills_past_its_capacity() -> None:
    """Otherwise a caller that goes quiet for a day would bank a day's worth of requests and
    the burst limit would mean nothing after any idle period."""
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock)
    await bucket.take("k", 5, 1.0)
    clock.advance(86_400)
    allowed = [(await bucket.take("k", 5, 1.0)).allowed for _ in range(6)]
    assert allowed == [True] * 5 + [False]


async def test_retry_after_reflects_the_refill_rate() -> None:
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock)
    await bucket.take("k", 1, 0.5)  # one token per two seconds
    decision = await bucket.take("k", 1, 0.5)
    assert decision.allowed is False
    assert decision.retry_after_seconds == pytest.approx(2.0)


async def test_retry_after_is_never_zero_seconds() -> None:
    """A ``Retry-After: 0`` invites the immediate retry the limit exists to stop."""
    assert BucketDecision(allowed=False, retry_after_seconds=0.2).retry_after_header == "1"
    assert BucketDecision(allowed=False, retry_after_seconds=3.2).retry_after_header == "4"


async def test_separate_keys_do_not_share_a_bucket() -> None:
    bucket = InMemoryTokenBucket(FakeClock())
    await bucket.take("a", 1, 1.0)
    assert (await bucket.take("b", 1, 1.0)).allowed is True


# ---- the fallback ------------------------------------------------------------------------


class _BrokenRunner:
    async def run(self, script, keys, args):  # noqa: ANN001, ANN201
        raise CountersUnavailable("down")

    async def close(self) -> None:
        return None


async def test_an_unreachable_redis_falls_back_to_the_local_bucket_not_to_allowing_everything():
    """The moment Redis is down is the worst moment to stop bounding a runaway caller: it is
    exactly when the infrastructure is already strained (FRD-405 §4.3)."""
    clock = FakeClock()
    bucket = FallbackTokenBucket(RedisTokenBucket(_BrokenRunner()), InMemoryTokenBucket(clock))

    allowed = [(await bucket.take("k", 2, 1.0)).allowed for _ in range(3)]

    assert allowed == [True, True, False]  # still bounded
    assert bucket.degraded is True


async def test_the_shared_bucket_is_used_when_redis_answers() -> None:
    class _Runner:
        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            return [0, 1500]

        async def close(self) -> None:
            return None

    bucket = FallbackTokenBucket(RedisTokenBucket(_Runner()), InMemoryTokenBucket(FakeClock()))
    decision = await bucket.take("k", 5, 1.0)

    assert decision.allowed is False
    assert decision.retry_after_seconds == pytest.approx(1.5)
    assert bucket.degraded is False


# ---- the service -------------------------------------------------------------------------


async def test_a_use_case_without_a_configured_limit_is_unlimited(sessionmaker) -> None:
    """FR-8: this feature must not start rejecting existing traffic on upgrade."""
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    for _ in range(100):
        await service.check("uc", "alice")


async def test_the_configured_limit_is_enforced(sessionmaker) -> None:
    await _limit(sessionmaker, limit_rpm=60, burst=2)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))

    await service.check("uc", "alice")
    await service.check("uc", "alice")
    with pytest.raises(RateLimited) as exc:
        await service.check("uc", "alice")
    assert "use case" in exc.value.message
    assert exc.value.retry_after == "1"


async def test_a_use_case_limit_is_shared_by_all_its_members(sessionmaker) -> None:
    await _limit(sessionmaker, limit_rpm=60, burst=2)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))

    await service.check("uc", "alice")
    await service.check("uc", "bob")
    with pytest.raises(RateLimited):
        await service.check("uc", "carol")


async def test_a_member_limit_only_binds_that_member(sessionmaker) -> None:
    await _limit(sessionmaker, scope="member", subject="alice", limit_rpm=60, burst=1)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))

    await service.check("uc", "alice")
    with pytest.raises(RateLimited) as exc:
        await service.check("uc", "alice")
    assert "member" in exc.value.message
    await service.check("uc", "bob")  # unaffected


async def test_both_scopes_apply_and_the_stricter_one_wins(sessionmaker) -> None:
    """FR-4: a member must not be able to spend the whole use case's allowance."""
    await _limit(sessionmaker, id=1, scope="use_case", limit_rpm=600, burst=10)
    await _limit(sessionmaker, id=2, scope="member", subject="alice", limit_rpm=60, burst=2)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))

    await service.check("uc", "alice")
    await service.check("uc", "alice")
    with pytest.raises(RateLimited) as exc:
        await service.check("uc", "alice")
    assert "member" in exc.value.message
    await service.check("uc", "bob")  # the wider use-case bucket still has room


async def test_a_disabled_limit_does_not_bind(sessionmaker) -> None:
    await _limit(sessionmaker, limit_rpm=1, burst=1, enabled=False)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    for _ in range(10):
        await service.check("uc", "alice")


async def test_an_unset_burst_defaults_to_the_per_minute_limit(sessionmaker) -> None:
    """ "60 per minute" with no burst must let 60 through, not zero."""
    await _limit(sessionmaker, limit_rpm=60, burst=0)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    for _ in range(60):
        await service.check("uc", "alice")
    with pytest.raises(RateLimited):
        await service.check("uc", "alice")


async def test_a_nonsensical_limit_is_ignored_rather_than_blocking_everything(sessionmaker):
    await _limit(sessionmaker, limit_rpm=0)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    for _ in range(10):
        await service.check("uc", "alice")


async def test_requests_without_a_use_case_are_not_rate_limited(sessionmaker) -> None:
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    await service.check(None, "alice")


async def test_enforcement_can_be_switched_off(sessionmaker) -> None:
    await _limit(sessionmaker, limit_rpm=1, burst=1)
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()), enforce=False)
    for _ in range(10):
        await service.check("uc", "alice")


async def test_the_config_cache_is_not_consulted_forever(sessionmaker) -> None:
    service = RateLimitService(sessionmaker, InMemoryTokenBucket(FakeClock()))
    await service.check("uc", "alice")  # caches "no limits"

    await _limit(sessionmaker, limit_rpm=60, burst=1)
    await service.check("uc", "alice")  # still cached, so still unlimited

    service.invalidate("uc")
    await service.check("uc", "alice")
    with pytest.raises(RateLimited):
        await service.check("uc", "alice")


# ---- distribution ------------------------------------------------------------------------


async def test_a_limit_arrives_from_management(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "ratelimit.upserted",
            {
                "id": 7,
                "use_case": "uc",
                "scope": "use_case",
                "subject": "",
                "limit_rpm": 120,
                "burst": 20,
                "enabled": True,
            },
        )
        stored = await session.get(RateLimitRead, 7)
    assert stored is not None
    assert (stored.limit_rpm, stored.burst) == (120, 20)


async def test_a_removed_limit_stops_binding(sessionmaker) -> None:
    await _limit(sessionmaker, id=7, limit_rpm=60, burst=1)
    async with sessionmaker() as session:
        await apply_event(session, "ratelimit.deleted", {"id": 7, "use_case": "uc"})
        assert await session.get(RateLimitRead, 7) is None
