"""Transport layer for the shared counters (ADR-0008).

This module's whole job is to be honest about failure: every caller has a documented fallback
that only works if unavailability is reported rather than swallowed. So that is what is tested
here — not Redis itself, which the integration suite exercises against a live server.
"""

from __future__ import annotations

import pytest

from aira_common.counters import (
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
