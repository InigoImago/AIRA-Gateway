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
from typing import Any

import stack_addresses

#: The demo's own use cases. Named rather than "everything", so running this against a stack that
#: also carries real traffic cannot quietly forgive somebody else's budget — and matched **exactly**
#: in both stores: `kundenservice` is not `kundenservice-eu`, nor a person called
#: `entwicklung.lead`.
DEMO_SLUGS = ("kundenservice", "entwicklung", "personalwesen", "coding-assistant")

#: Override the two stores for a stack this machine does not publish.
DSN_VARIABLE = "AIRA_DEMO_RESET_DSN"
REDIS_VARIABLE = "AIRA_DEMO_RESET_REDIS"

#: The characters Redis' `SCAN MATCH` treats as a pattern rather than as themselves.
_GLOB = str.maketrans({character: f"\\{character}" for character in "\\*?[]"})


def postgres_url() -> str:
    """The gateway's database on the Postgres **this** stack publishes.

    Resolved as Compose resolves it — published port, account, database name — because a second
    stack beside the first (`AIRA_STACK=b`, its ports moved) is exactly where a written-down
    `localhost:5432` resets the wrong demo and moves the wrong rows' clocks.
    """
    return os.environ.get(DSN_VARIABLE) or stack_addresses.postgres_url(
        stack_addresses.setting("AIRA_POSTGRES_DB")
    )


def redis_url() -> str:
    """The Redis this stack publishes, for the same reason as :func:`postgres_url`."""
    return os.environ.get(REDIS_VARIABLE) or stack_addresses.redis_url()


def redis_patterns(slug: str) -> list[str]:
    """Every Redis key `slug`'s consumption can live under, as `SCAN MATCH` patterns.

    Built with the gateway's own key builders, so this follows the key format instead of restating
    it: the budget reservations (`budget:uc:<slug>:<period>` and
    `budget:member:<slug>:<person>:<period>`) and the rate-limit buckets (`rl:{<slug>}:uc` and
    `rl:{<slug>}:member:<person>`). The use case sits between separators in every one of them, and
    a key that merely *contains* the slug is somebody else's.
    """
    from aira_gateway.budgets.ledger import _key as ledger_key
    from aira_gateway.scopes import Scope

    whole, member = Scope(slug), Scope(slug, "")
    return [
        ledger_key(whole.usage_key.translate(_GLOB), "*"),
        ledger_key(member.usage_key.translate(_GLOB) + "*", "*"),
        whole.bucket_key.translate(_GLOB),
        member.bucket_key.translate(_GLOB) + "*",
    ]


async def _clear_postgres() -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(postgres_url())
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


async def _clear_redis(client: Any = None) -> int:
    """Delete the demo's reservations and rate-limit buckets; how many keys went.

    Both families: a bucket left full would refuse the first burst of the run this is preparing.
    """
    if client is None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url())
    try:
        removed = 0
        for slug in DEMO_SLUGS:
            for pattern in redis_patterns(slug):
                async for key in client.scan_iter(match=pattern):
                    removed += await client.delete(key)
        return removed
    finally:
        await client.aclose()


async def reset() -> tuple[int, int]:
    """Clear both stores and say how much was cleared. Raises; the caller decides what that means.

    Separate from :func:`main` because `demo_history.py` calls it **once per simulated day** — each
    backdated day is its own budget period, so clearing between them is what would have
    happened — and twenty-eight copies of the summary line is not a report.
    """
    return await _clear_postgres(), await _clear_redis()


async def main() -> int:
    try:
        rows, keys = await reset()
    except Exception as error:  # noqa: BLE001 - a demo helper reports and does not raise
        print(f"could not reset the demo counters: {error}", file=sys.stderr)
        # Not fatal: the traffic that follows still runs, it just may find a spent budget. Saying
        # so beats stopping the whole showcase over a convenience.
        return 0

    print(f"  reset {rows} budget counter(s) and {keys} shared counter(s) for the demo use cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
