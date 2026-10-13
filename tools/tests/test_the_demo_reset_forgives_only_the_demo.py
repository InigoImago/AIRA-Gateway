"""`demo_reset_usage` clears the demo's own consumption, on its own stack, and nothing else.

It runs on every `make showcase` and once per simulated day of `demo_history.py`. Two defects:

- **Redis was matched by substring** (`SCAN MATCH *<slug>*`), so the reset deleted the budget
  reservations and rate-limit buckets of any use case or person whose key merely *contained* a demo
  slug — `kundenservice-eu`, `team-personalwesen`, `entwicklung.lead@corp` — while the docstring
  promised it could not forgive somebody else's budget. Only the Postgres half was checked.
- **Both stores were `localhost:5432` and `localhost:6379`**, written down. A second stack beside
  the first (`AIRA_STACK=b`, ports moved, as the compose files invite) reset the *first* stack's
  demo, and `demo_history` moved the clocks of the wrong database's rows.
"""

from __future__ import annotations

import demo_reset_usage
import fakeredis
import pytest

#: Keys the demo's traffic writes, one of each family.
DEMO_KEYS = {
    "budget:uc:kundenservice:2026-10",
    "budget:member:entwicklung:anna.schmidt:2026-10-12",
    "rl:{personalwesen}:uc",
    "rl:{coding-assistant}:member:bob",
}
#: Keys that contain a demo slug and belong to somebody else.
OTHER_KEYS = {
    "budget:uc:kundenservice-eu:2026-10",
    "budget:uc:team-personalwesen:2026-10-12",
    "budget:member:payroll:entwicklung.lead@corp:2026-10",
    "rl:{kundenservice-eu}:uc",
    "rl:{payroll}:member:entwicklung.lead@corp",
    "rl:{installation}:all",
}


@pytest.mark.asyncio
async def test_only_the_demos_own_counters_are_cleared() -> None:
    server = fakeredis.FakeServer()
    seed = fakeredis.FakeAsyncRedis(server=server)
    for key in DEMO_KEYS | OTHER_KEYS:
        await seed.set(key, 1)

    removed = await demo_reset_usage._clear_redis(fakeredis.FakeAsyncRedis(server=server))

    left = {key.decode() for key in await seed.keys("*")}
    await seed.aclose()
    assert left == OTHER_KEYS, f"somebody else's counters were cleared: {sorted(OTHER_KEYS - left)}"
    assert removed == len(DEMO_KEYS)


def test_a_slug_is_matched_as_itself_and_never_as_a_pattern() -> None:
    """`SCAN MATCH` reads `*`, `?` and `[` as wildcards; a slug carrying one would match others."""
    patterns = demo_reset_usage.redis_patterns("a*b")

    assert all("a\\*b" in pattern for pattern in patterns), patterns


def test_both_stores_are_the_ones_this_stack_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(demo_reset_usage.DSN_VARIABLE, raising=False)
    monkeypatch.delenv(demo_reset_usage.REDIS_VARIABLE, raising=False)
    monkeypatch.setenv("AIRA_PUBLISH_POSTGRES_PORT", "15432")
    monkeypatch.setenv("AIRA_PUBLISH_REDIS_PORT", "16379")

    assert ":15432/" in demo_reset_usage.postgres_url()
    assert ":16379/" in demo_reset_usage.redis_url()


def test_an_explicit_address_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(demo_reset_usage.DSN_VARIABLE, "postgresql+psycopg://x:y@db.test/g")
    monkeypatch.setenv(demo_reset_usage.REDIS_VARIABLE, "redis://cache.test/1")

    assert demo_reset_usage.postgres_url() == "postgresql+psycopg://x:y@db.test/g"
    assert demo_reset_usage.redis_url() == "redis://cache.test/1"
