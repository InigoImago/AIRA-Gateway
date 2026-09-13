"""Retention for stored request payloads (FRD-404).

``request_logs`` keeps what a caller sent and what the model answered — the most useful material
for an incident and the most sensitive thing AIRA holds. Two clocks, deliberately separate:

- **Payload retention** — per use case, default one week. After it the bodies are removed and the
  row keeps its metadata, which the spend and usage reporting reads.
- **Record retention** — installation-wide, off by default (``AIRA_LOG_RETENTION_DAYS``). When set,
  whole rows older than that are removed.

Run it periodically: ``python -m aira_gateway.retention``.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_gateway.db.models import RequestLog, RetentionRun, UseCaseRead

DEFAULT_RETENTION_DAYS = 7

_log = get_logger("aira_gateway.retention")


def _affected(result: object) -> int:
    """Rows touched by an UPDATE/DELETE. Both return a cursor result; the generic ``Result``
    type does not declare ``rowcount``."""
    return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0


@dataclass(frozen=True, slots=True)
class PruneResult:
    """What one pass removed."""

    payloads_cleared: int
    rows_deleted: int

    def __str__(self) -> str:
        return f"payloads cleared: {self.payloads_cleared}, rows deleted: {self.rows_deleted}"


class RetentionService:
    """Applies each use case's retention period to the stored payloads."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        default_retention_days: int = DEFAULT_RETENTION_DAYS,
        log_retention_days: int = 0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._default_retention_days = max(1, default_retention_days)
        self._log_retention_days = max(0, log_retention_days)

    async def prune(self, now: datetime | None = None) -> PruneResult:
        """Remove payloads past their retention, and rows past the record retention.

        A use case that has switched storage off is treated as a period of zero: turning the
        toggle off means "do not keep prompts", so whatever is already there goes on the next
        run rather than lingering for the remainder of the old period.
        """
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            periods = await self._periods(session)

            cleared = 0
            for use_case, days in periods.items():
                cutoff = now if days is None else now - timedelta(days=days)
                cleared += await self._clear_payloads(session, use_case, cutoff)

            # Rows with no use case, **and rows whose slug the read-model does not name** (a purged
            # use case, or one whose row has not arrived yet) follow the installation default.
            # The default rather than zero: Kafka orders nothing against the use-case topic, so a
            # use case not yet arrived looks exactly like a deleted one.
            cleared += await self._clear_payloads(
                session,
                None,
                now - timedelta(days=self._default_retention_days),
                unknown=set(periods),
            )

            deleted = 0
            if self._log_retention_days:
                removed = await session.execute(
                    delete(RequestLog).where(
                        RequestLog.created_at < now - timedelta(days=self._log_retention_days)
                    )
                )
                deleted = _affected(removed)

            # The pass leaves a row (`FRD-608` §2.4), in the same transaction as the deletions it
            # describes — a record of an erasure that rolled back would be worse than none. Stamped
            # with the `now` the sweep decided with.
            session.add(
                RetentionRun(
                    ran_at=now,
                    payloads_cleared=cleared,
                    rows_deleted=deleted,
                )
            )
            await session.commit()

        outcome = PruneResult(payloads_cleared=cleared, rows_deleted=deleted)
        _log.info(
            "retention_pruned",
            payloads_cleared=outcome.payloads_cleared,
            rows_deleted=outcome.rows_deleted,
            use_cases=len(periods),
        )
        return outcome

    async def _periods(self, session: AsyncSession) -> dict[str, int | None]:
        """Retention period per use case; ``None`` where storage is switched off entirely.

        Retired use cases are included (`FRD-607`): their tombstones keep the period the data
        subject was told about — neither swept early nor handed the installation default.
        """
        result = await session.execute(
            select(UseCaseRead.slug, UseCaseRead.retention_days, UseCaseRead.store_payloads)
        )
        return {
            slug: (None if not store else max(1, days or self._default_retention_days))
            for slug, days, store in result.all()
        }

    async def _clear_payloads(
        self,
        session: AsyncSession,
        use_case: str | None,
        cutoff: datetime,
        *,
        unknown: set[str] | None = None,
    ) -> int:
        """Strip the bodies of rows older than ``cutoff``, keeping their metadata.

        Only rows that still have a payload are touched, so repeated runs are cheap and the count
        is what was actually cleared. ``unknown`` widens the ``use_case is None`` pass to every slug
        **outside** that set.
        """
        criterion = (
            RequestLog.use_case.is_(None) if use_case is None else RequestLog.use_case == use_case
        )
        if use_case is None and unknown is not None:
            criterion = criterion | RequestLog.use_case.not_in(unknown)
        result = await session.execute(
            update(RequestLog)
            .where(
                criterion,
                RequestLog.created_at < cutoff,
                (RequestLog.request_payload.is_not(None))
                | (RequestLog.response_payload.is_not(None)),
            )
            .values(request_payload=None, response_payload=None)
        )
        return _affected(result)


async def _run() -> PruneResult:  # pragma: no cover - thin process wrapper
    from aira_gateway.config import GatewaySettings, configure_process
    from aira_gateway.db.base import build_engine, build_sessionmaker

    settings = GatewaySettings()
    # An erasure nobody can see afterwards is the one thing this sweep must not be (`FRD-615`).
    configure_process(settings)
    engine = build_engine(settings.database_url(use_sqlite=False))
    try:
        service = RetentionService(
            build_sessionmaker(engine),
            default_retention_days=settings.default_retention_days,
            log_retention_days=settings.log_retention_days,
        )
        return await service.prune()
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin process wrapper
    result = asyncio.run(_run())
    print(f"[retention] {result}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
