"""Retention for stored request payloads (FRD-404).

``request_logs`` keeps what a caller sent and what the model answered. That is the most useful
material for investigating an incident and, at the same time, the most sensitive thing AIRA
holds: prompts routinely contain personal data, and nothing deleted it. This module is what
turns "we store everything" into "we store it for as long as the use case says".

Two clocks, deliberately separate:

- **Payload retention** — per use case, default one week. After it, the request and response
  bodies are removed and the row keeps only its metadata (who, when, model, tokens, latency,
  cost). That metadata is what the spend and usage reporting reads; deleting whole rows on the
  same short clock would silently blind it after a week.
- **Record retention** — installation-wide, off by default (``AIRA_LOG_RETENTION_DAYS``). When
  set, whole rows older than that are removed. Opt in once the reporting horizon is decided.

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
from aira_gateway.db.models import RequestLog, UseCaseRead

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
        """Remove payloads past their retention, and rows past the record retention."""
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            periods = await self._periods(session)

            cleared = 0
            for use_case, days in periods.items():
                cleared += await self._clear_payloads(session, use_case, now - timedelta(days=days))

            # Requests that carry no use case (unbound break-glass keys, demo traffic) follow the
            # installation default — they are not exempt just because nobody claimed them.
            cleared += await self._clear_payloads(
                session, None, now - timedelta(days=self._default_retention_days)
            )

            deleted = 0
            if self._log_retention_days:
                removed = await session.execute(
                    delete(RequestLog).where(
                        RequestLog.created_at < now - timedelta(days=self._log_retention_days)
                    )
                )
                deleted = _affected(removed)

            await session.commit()

        outcome = PruneResult(payloads_cleared=cleared, rows_deleted=deleted)
        _log.info(
            "retention_pruned",
            payloads_cleared=outcome.payloads_cleared,
            rows_deleted=outcome.rows_deleted,
            use_cases=len(periods),
        )
        return outcome

    async def _periods(self, session: AsyncSession) -> dict[str, int]:
        """Retention period per use case, from the read-model Management feeds."""
        result = await session.execute(select(UseCaseRead.slug, UseCaseRead.retention_days))
        return {slug: max(1, days or self._default_retention_days) for slug, days in result.all()}

    async def _clear_payloads(
        self, session: AsyncSession, use_case: str | None, cutoff: datetime
    ) -> int:
        """Strip the bodies of rows older than ``cutoff``, keeping their metadata.

        Only rows that still have a payload are touched, so repeated runs are cheap and the
        reported count is the number actually cleared rather than the number matched.
        """
        criterion = (
            RequestLog.use_case.is_(None) if use_case is None else RequestLog.use_case == use_case
        )
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
    from aira_gateway.config import GatewaySettings
    from aira_gateway.db.base import build_engine, build_sessionmaker

    settings = GatewaySettings()
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
