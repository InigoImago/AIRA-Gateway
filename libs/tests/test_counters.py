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
