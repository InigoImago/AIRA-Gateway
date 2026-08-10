"""Clear what the demo has *consumed*, never what it is *configured* to be (`FRD-130`).

`make showcase` is supposed to tell the same story every time it is run. It did not: budgets are
deliberately calibrated so a handful of requests moves each bar into the middle of its range, so
the second run of the day found them spent and answered **429 to six of ten requests** — including
the prompt-injection case, whose entire point is to be refused by the *pipeline* and which then
reported a budget refusal instead. The demonstration was still telling the truth, and it was
telling it about yesterday.

So this resets the counters and nothing else. Use cases, budgets, limits, rules, keys and the
audit trail are all untouched — the request log in particular, because the spend report reading
zero after every showcase run would be the opposite defect.

**Both stores, or neither.** `budget_usage` in Postgres is the system of record and the shared
Redis counters are what the pre-dispatch guard actually reads (`FRD-405`); clearing one leaves the
other to refuse traffic for a period nobody can see. Redis holds them for five minutes and rebuilds
from Postgres on a miss, so the order matters: Postgres first, then Redis.

Demo only, and it says so — this is `make showcase`'s idempotence, not a product feature. There is
no supported way to forgive a budget, and there should not be.
"""

from __future__ import annotations

import asyncio
import os
import sys

#: The demo's own use cases. Named rather than "everything", so running this against a stack that
#: also carries real traffic cannot quietly forgive somebody else's budget.
DEMO_SLUGS = ("kundenservice", "entwicklung", "personalwesen", "coding-assistant")

POSTGRES = os.environ.get(
    "AIRA_DEMO_RESET_DSN", "postgresql+psycopg://aira:aira-local@localhost:5432/aira_gateway"
)
REDIS_URL = os.environ.get("AIRA_DEMO_RESET_REDIS", "redis://localhost:6379/0")


async def _clear_postgres() -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(POSTGRES)
    try:
        async with engine.begin() as connection:
            # `scope_key` is `uc:<slug>` for a whole use case and `member:<slug>:<subject>`
            # for one person (`FRD-405`), so both are matched by prefix rather than by a column
            # that does not exist — the shape is the key, and reading it from the table beats
            # assuming it.
            result = await connection.execute(
                text("DELETE FROM budget_usage WHERE split_part(scope_key, ':', 2) = ANY(:slugs)"),
                {"slugs": list(DEMO_SLUGS)},
            )
            return result.rowcount or 0
    finally:
        await engine.dispose()


async def _clear_redis() -> int:
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL)
    try:
        removed = 0
        for slug in DEMO_SLUGS:
            # Both families: the budget reservations and the rate-limit buckets. A bucket left
            # full would refuse the first burst of the run this is preparing.
            async for key in client.scan_iter(match=f"*{slug}*"):
                await client.delete(key)
                removed += 1
        return removed
    finally:
        await client.aclose()


async def main() -> int:
    try:
        rows = await _clear_postgres()
        keys = await _clear_redis()
    except Exception as error:  # noqa: BLE001 - a demo helper reports and does not raise
        print(f"could not reset the demo counters: {error}", file=sys.stderr)
        # Not fatal: the traffic that follows still runs, it just may find a spent budget. Saying
        # so beats stopping the whole showcase over a convenience.
        return 0

    print(f"  reset {rows} budget counter(s) and {keys} shared counter(s) for the demo use cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
