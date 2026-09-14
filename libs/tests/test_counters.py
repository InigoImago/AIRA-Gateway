"""Transport layer for the shared counters (ADR-0008).

This module's whole job is to be honest about failure: every caller has a documented fallback
that only works if unavailability is reported rather than swallowed. So that is what is tested
here — not Redis itself, which the integration suite exercises against a live server.
"""

from __future__ import annotations

import pytest

from aira_common.counters import (
    RETRY_AFTER_FAILURE_SECONDS,
    CountersUnavailable,
    DisabledRunner,
    RedisRunner,
    build_runner,
)


async def test_no_url_configured_yields_a_runner_that_reports_unavailable() -> None:
    runner = build_runner("")
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])
    await runner.close()


async def test_a_whitespace_url_counts_as_unconfigured() -> None:
    assert isinstance(build_runner("   "), DisabledRunner)


async def test_a_configured_url_yields_a_redis_runner() -> None:
    assert isinstance(build_runner("redis://localhost:6379/0"), RedisRunner)


async def test_an_unreachable_server_reports_unavailable_rather_than_raising_redis_errors() -> None:
    """Callers catch ``CountersUnavailable``; a leaking ConnectionError would bypass every
    fallback in FRD-405 §4.3 and surface to the user as a 500."""
    runner = RedisRunner("redis://127.0.0.1:6390/0", connect_timeout=0.05)
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])
    await runner.close()


async def test_a_failure_opens_the_circuit_so_the_next_call_does_not_wait_again() -> None:
    """Without this, every request pays a connection timeout while Redis is down — a degraded
    dependency would become a slow gateway, which is what the fallbacks exist to prevent."""
    runner = RedisRunner("redis://127.0.0.1:6390/0", connect_timeout=0.05)
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])

    import time

    started = time.monotonic()
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])
    assert time.monotonic() - started < 0.02  # short-circuited, no second connection attempt
    await runner.close()


async def test_closing_a_runner_that_never_connected_is_not_an_error() -> None:
    await RedisRunner("redis://127.0.0.1:6390/0").close()
    await DisabledRunner().close()


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_the_breaker_lets_go_again_once_the_window_has_passed() -> None:
    """The other half of the circuit breaker, and the half a green suite cannot distinguish from
    a broken one: only the short-circuit was tested, so a breaker that never reopened would have
    left every caller permanently degraded with nothing to show for it."""
    clock = FakeClock()
    runner = RedisRunner("redis://127.0.0.1:6390/0", connect_timeout=0.05, clock=clock)

    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])
    with pytest.raises(CountersUnavailable, match="not retrying yet"):
        await runner.run("return 1", [], [])

    clock.advance(RETRY_AFTER_FAILURE_SECONDS + 0.1)

    # It tries again — and reports the connection failure rather than the short-circuit.
    with pytest.raises(CountersUnavailable) as exc:
        await runner.run("return 1", [], [])
    assert "not retrying yet" not in str(exc.value)
    await runner.close()


async def test_a_success_closes_the_breaker_immediately() -> None:
    """Recovery must not wait out the window: the moment Redis answers, the next call goes
    through rather than being refused by a stale failure."""
    clock = FakeClock()
    runner = RedisRunner("redis://127.0.0.1:6390/0", clock=clock)

    class _Script:
        async def __call__(self, keys, args):  # noqa: ANN001, ANN204
            return 1

    class _Client:
        def register_script(self, script):  # noqa: ANN001, ANN204
            return _Script()

        async def aclose(self) -> None:
            return None

    runner._unavailable_until = clock() + RETRY_AFTER_FAILURE_SECONDS  # a previous failure
    runner._client = _Client()
    clock.advance(RETRY_AFTER_FAILURE_SECONDS + 0.1)

    assert await runner.run("return 1", [], []) == 1
    assert runner._unavailable_until == 0.0  # closed, so the next call is not short-circuited
    await runner.close()


# ---- Redis Sentinel ------------------------------------------------------------------------------

from aira_common.counters import SentinelConfig, SentinelConfigError, parse_sentinels  # noqa: E402


def test_sentinel_addresses_are_parsed_and_a_bad_one_is_refused() -> None:
    assert parse_sentinels("redis-a:26379, redis-b:26379,") == (
        ("redis-a", 26379),
        ("redis-b", 26379),
    )
    for bad in ("redis-a", "redis-a:", ":26379", "redis-a:port", "redis-a:70000"):
        with pytest.raises(SentinelConfigError):
            parse_sentinels(bad)


def test_a_log_line_names_the_sentinels_and_never_a_password() -> None:
    config = SentinelConfig(
        sentinels=(("redis-a", 26379),),
        service="aira",
        password="data-secret",
        sentinel_password="sentinel-secret",
    )
    assert config.target() == "sentinel://redis-a:26379/aira"
    assert "secret" not in config.target()


async def test_sentinels_are_chosen_over_a_url() -> None:
    config = SentinelConfig(sentinels=(("redis-a", 26379),), service="aira")
    runner = build_runner("redis://localhost:6379/0", config)
    assert isinstance(runner, RedisRunner)
    assert runner._target == "sentinel://redis-a:26379/aira"  # noqa: SLF001


async def test_the_leader_is_asked_of_the_sentinels_with_the_credentials(monkeypatch) -> None:
    """The client is the one the sentinels hand out for the service, with the data password and
    database, and the sentinels are asked with their own password."""
    seen: dict = {}

    class Script:
        async def __call__(self, keys, args):
            return [keys, args]

    class Leader:
        def register_script(self, script):
            seen["script"] = script
            return Script()

    class FakeSentinel:
        def __init__(self, sentinels, sentinel_kwargs=None, **kwargs):
            seen["sentinels"] = sentinels
            seen["sentinel_kwargs"] = sentinel_kwargs
            seen["connection"] = kwargs

        def master_for(self, service, **kwargs):
            seen["service"] = service
            seen["leader"] = kwargs
            return Leader()

    import redis.asyncio.sentinel as sentinel_module

    monkeypatch.setattr(sentinel_module, "Sentinel", FakeSentinel)
    runner = RedisRunner(
        sentinel=SentinelConfig(
            sentinels=(("redis-a", 26379), ("redis-b", 26379)),
            service="aira",
            username="aira",
            password="data-secret",
            sentinel_password="sentinel-secret",
            db=2,
        )
    )

    assert await runner.run("return 1", ["k"], [1]) == [["k"], [1]]
    assert seen["sentinels"] == [("redis-a", 26379), ("redis-b", 26379)]
    assert seen["sentinel_kwargs"]["password"] == "sentinel-secret"
    assert seen["service"] == "aira"
    assert (seen["leader"]["username"], seen["leader"]["password"]) == ("aira", "data-secret")
    assert (seen["leader"]["db"], seen["leader"]["decode_responses"]) == (2, True)


async def test_sentinels_nobody_can_reach_report_unavailable_rather_than_raising() -> None:
    """The promise a URL makes: callers catch `CountersUnavailable` and take their fallback."""
    runner = RedisRunner(
        sentinel=SentinelConfig(sentinels=(("127.0.0.1", 26390),), service="aira"),
        connect_timeout=0.05,
    )
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", [], [])
    await runner.close()
