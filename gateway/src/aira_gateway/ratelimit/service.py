"""Request-rate enforcement per use case and per member (FRD-405).

Called pre-dispatch, before any upstream work is done and before the budget is touched: the
point of a limit is to make the expensive part of the request never happen.

Both scopes are checked where both exist and the stricter one wins, so a single member cannot
consume a whole use case's allowance. A use case with no configured limit is unlimited, exactly
as before this feature existed — this must never start rejecting traffic on upgrade.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.db.models import RateLimitRead
from aira_gateway.ratelimit.buckets import BucketRequest, TokenBucket, per_minute
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.scopes import Scope

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
        #: Read by the pre-authentication bound (`auth/attempts.py`), which needs the same bucket
        #: implementation — and therefore the same Redis-or-per-instance degradation — without
        #: needing a configured limit record to look up.
        self.bucket = bucket
        self._enforce = enforce
        # Injectable so the cache's *expiry* can be tested rather than only its manual
        # invalidation — a TTL nothing ever crosses is a TTL nothing tests.
        self._clock = clock
        self._cache: dict[str, tuple[float, list[RateLimitRead]]] = {}

    async def check(
        self,
        use_case: str | None,
        subject: str | None,
        units: int = 1,
        *,
        extra: Sequence[BucketRequest] = (),
    ) -> None:
        """Raise :class:`RateLimited` if the caller is over its configured rate.

        ``units`` is what the request weighs — one for an ordinary call, one per text for an
        embedding batch (`FRD-113` FR-6). Admitting a batch of 500 as a single request would leave
        a limit of 10 per minute allowing 5 000 texts per minute; the limit would be intact on
        paper and gone in practice, which is a control bypass rather than an inaccuracy.
        """
        # A throttle from `FRD-503` is an *extra* bucket, not a replacement: a throttled caller
        # is still subject to whatever limits already applied, and the two are taken together so
        # the decision stays all-or-nothing (FR-4).
        if not self._enforce:
            return
        configured = (
            self._applicable(await self._config(use_case), use_case, subject) if use_case else []
        )
        buckets = [*configured, *extra]
        if not buckets:
            return

        # A batch larger than the bucket itself can never be admitted, however long the caller
        # waits — so it is refused with a message that says so instead of a `Retry-After` that
        # would still be wrong an hour later. `FRD-113` §11: the batch bound and the configured
        # limits interact, and the failure has to name which of the two refused.
        for bucket in buckets:
            if units > bucket.capacity:
                raise RateLimited(
                    f"A request weighing {units} cannot fit the {bucket.capacity}-request "
                    f"allowance of the {bucket.label}. Send it in smaller batches, or raise the "
                    "limit.",
                    retry_after="1",
                )

        # One call, all or nothing: a refused request must not have debited the buckets that
        # would have granted it (FRD-405 FR-4).
        decision = await self._bucket.take(buckets, units)
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
        self,
        records: list[RateLimitRead],
        use_case: str,
        subject: str | None,
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
            scope = Scope.applying(scope=record.scope, use_case=use_case, caller=subject)
            if scope is None:
                continue
            buckets.append(
                per_minute(
                    scope.bucket_key, record.limit_rpm, label=scope.label, burst=record.burst
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
    """Bucket size, as :func:`per_minute` resolves it.

    Kept as a name of its own because the tests and the refusal message both ask "how big is this
    bucket", and answering it by restating the rule is how the two came to disagree elsewhere.
    """
    return per_minute("", record.limit_rpm, burst=record.burst).capacity
