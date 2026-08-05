"""Request-rate enforcement per use case and per member (FRD-405).

Called pre-dispatch, before any upstream work is done and before the budget is touched: the
point of a limit is to make the expensive part of the request never happen.

Both scopes are checked where both exist and the stricter one wins, so a single member cannot
consume a whole use case's allowance. A use case with no configured limit is unlimited, exactly
as before this feature existed — this must never start rejecting traffic on upgrade.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.db.models import RateLimitRead
from aira_gateway.ratelimit.buckets import BucketRequest, TokenBucket
from aira_gateway.ratelimit.errors import RateLimited

_log = get_logger("aira_gateway.ratelimit")

# How long a loaded set of limits is reused before re-reading it.
#
# This exists because the check is on the hot path and a request already costs six or seven
# database round trips; adding a seventh for configuration that changes a few times a year would
# work against the throughput this feature is meant to protect. Limits arrive over Kafka and are
# rare, so a few seconds of staleness after an edit is not a meaningful property to give up.
CONFIG_CACHE_SECONDS = 5.0


class RateLimitService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        bucket: TokenBucket,
        *,
        enforce: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._bucket = bucket
        self._enforce = enforce
        # Injectable so the cache's *expiry* can be tested rather than only its manual
        # invalidation — a TTL nothing ever crosses is a TTL nothing tests.
        self._clock = clock
        self._cache: dict[str, tuple[float, list[RateLimitRead]]] = {}

    async def check(self, use_case: str | None, subject: str | None) -> None:
        """Raise :class:`RateLimited` if the caller is over its configured rate."""
        if not self._enforce or not use_case:
            return
        buckets = self._applicable(await self._config(use_case), use_case, subject)
        if not buckets:
            return

        # One call, all or nothing: a refused request must not have debited the buckets that
        # would have granted it (FRD-405 FR-4).
        decision = await self._bucket.take(buckets)
        if decision.allowed:
            return

        label = decision.refused.label if decision.refused else "use case"
        _log.info(
            "rate_limited",
            use_case=use_case,
            subject=subject,
            scope=label,
            retry_after=decision.retry_after_header,
        )
        raise RateLimited(
            f"Request rate limit exceeded for {label}.",
            retry_after=decision.retry_after_header,
        )

    def _applicable(
        self, records: list[RateLimitRead], use_case: str, subject: str | None
    ) -> list[BucketRequest]:
        """Turn the configured records into the buckets this request must pass.

        Both the use-case and the member bucket are returned where both apply. Checking only the
        narrower one would let a single member spend the whole use case's allowance; checking only
        the wider one would make a per-member limit decorative. They are returned together rather
        than checked one at a time so the decision can be all-or-nothing.
        """
        buckets: list[BucketRequest] = []
        for record in records:
            if not record.enabled or record.limit_rpm <= 0:
                continue
            if record.scope == "use_case":
                buckets.append(
                    BucketRequest(
                        key=bucket_key(use_case),
                        capacity=_capacity(record),
                        refill_per_second=record.limit_rpm / 60,
                        label="use case",
                    )
                )
            elif record.scope == "member" and subject and record.subject == subject:
                buckets.append(
                    BucketRequest(
                        key=bucket_key(use_case, subject),
                        capacity=_capacity(record),
                        refill_per_second=record.limit_rpm / 60,
                        label="member",
                    )
                )
        return buckets

    async def _config(self, use_case: str) -> list[RateLimitRead]:
        cached = self._cache.get(use_case)
        now = self._clock()
        if cached is not None and now < cached[0]:
            return cached[1]
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RateLimitRead).where(RateLimitRead.use_case == use_case)
            )
            records = list(result.scalars())
        self._cache[use_case] = (now + CONFIG_CACHE_SECONDS, records)
        return records

    def invalidate(self, use_case: str | None = None) -> None:
        """Drop cached configuration. Used by the tests and available to the consumer."""
        if use_case is None:
            self._cache.clear()
        else:
            self._cache.pop(use_case, None)


def _capacity(record: RateLimitRead) -> int:
    """Bucket size. An unset burst means the limit itself, so a plain "60 per minute" behaves
    the way someone reading it expects rather than allowing nothing through at once."""
    return record.burst if record.burst and record.burst > 0 else record.limit_rpm


def bucket_key(use_case: str, subject: str | None = None) -> str:
    """The Redis key for a use-case or member bucket.

    The use case sits in a hash tag so that every bucket a single request must pass hashes to the
    same slot. A multi-key script is what makes the all-or-nothing decision possible, and Redis
    Cluster refuses one whose keys live on different nodes — so this is what keeps the design
    valid if the counter store is ever clustered.
    """
    return f"rl:{{{use_case}}}:member:{subject}" if subject else f"rl:{{{use_case}}}:uc"
