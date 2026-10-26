"""Request-rate enforcement per use case and per member (`FRD-405`).

Called pre-dispatch, before any upstream work and before the budget is touched: the point of a
limit is that the expensive part of the request never happens. Where both scopes apply both are
checked, so one member cannot consume a whole use case's allowance. A use case with no configured
limit is unlimited, as before this feature existed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.db.models import RateLimitRead
from aira_gateway.ratelimit.buckets import BucketRequest, TokenBucket, per_minute
from aira_gateway.ratelimit.errors import RateLimited, TooHeavyForTheLimit
from aira_gateway.scopes import Scope, binding

_log = get_logger("aira_gateway.ratelimit")

#: How long a loaded set of limits is reused before re-reading it. The check is on the hot path,
#: and limits arrive over Kafka a few times a year; a few seconds of staleness costs nothing.
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
        #: Read by the pre-authentication bound (`auth/attempts.py`), which needs the same bucket —
        #: and the same Redis-or-per-instance degradation — without a configured limit.
        self.bucket = bucket
        self._enforce = enforce
        # Injectable so the cache's expiry can be tested, not only its invalidation.
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

        ``units`` is what the request weighs — one, or one per text of an embedding batch
        (`FRD-113` FR-6). ``extra`` carries a `FRD-503` throttle: an added bucket, not a
        replacement, taken together with the configured ones so the decision stays all-or-nothing.

        **The switch is the configured limits', not the throttle's.** With rate limits off
        (`AIRA_ENFORCE_RATE_LIMITS=false`) no configured limit is taken and a throttle still is: it
        is a suspension, and `AIRA_ENFORCE_SUSPENSIONS` is the switch that turns those off.
        """
        configured = (
            self._applicable(await self._config(use_case), use_case, subject)
            if use_case and self._enforce
            else []
        )
        buckets = [*configured, *extra]
        if not buckets:
            return

        # A batch larger than a bucket can never be admitted, however long the caller waits, so it
        # is refused by name rather than with a `Retry-After` that stays wrong (`FRD-113` §11).
        for bucket in buckets:
            if units > bucket.capacity:
                raise TooHeavyForTheLimit(
                    f"A request weighing {units} cannot fit the {bucket.capacity}-request "
                    f"allowance of the {bucket.label}. Send it in smaller batches, or raise the "
                    "limit."
                )

        # One call, all or nothing (`FRD-405` FR-4).
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
            bucket=decision.refused.key if decision.refused else None,
        )

    def _applicable(
        self,
        records: list[RateLimitRead],
        use_case: str,
        subject: str | None,
    ) -> list[BucketRequest]:
        """The buckets this request must pass: every enabled limit whose scope binds it.

        The use-case and the member bucket are both returned where both apply — only the narrower
        would let one member spend the use case's allowance, only the wider would make a member
        limit decorative. Which per-person row binds is `scopes.binding`'s answer, the one the
        budgets follow: a rate limit has no period, so its per-person rows share one slot.
        """
        enabled = [record for record in records if record.enabled and record.limit_rpm > 0]
        buckets: list[BucketRequest] = []
        for record in binding(enabled, caller=subject, slot=_one_slot):
            scope = Scope.applying(
                scope=record.scope, use_case=use_case, caller=subject, named=record.subject
            )
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


def _one_slot(record: RateLimitRead) -> str:
    """A rate limit's per-person rows hold one slot: they share one bucket per person."""
    return ""
