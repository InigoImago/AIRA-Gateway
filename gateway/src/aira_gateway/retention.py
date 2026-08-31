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

            # Requests that carry no use case (unbound break-glass keys, demo traffic) follow the
            # installation default — they are not exempt just because nobody claimed them.
            #
            # **And so does everything else the read-model does not name.** The loop above covers
            # the use cases this gateway knows and the pass here covers rows with no use case at
            # all; a row whose slug is *neither* matched nothing and was never cleared. That is not
            # an edge case — it is what deleting a use case produces, and `_delete_usecase` keeps
            # the rows on purpose while stating that "their payloads still expire on the retention
            # clock". They did not. Measured on a running stack: 1509 rows carrying stored prompts
            # for use cases that no longer existed, on the one clock nothing would ever wind.
            # (Since `FRD-607` a retired use case keeps its row and its own period, so this pass
            # now catches only genuinely unknown slugs — a **purged** use case, or one whose row
            # has not arrived. It stays because both still happen.)
            #
            # The default rather than zero, because a slug the read-model does not name is
            # **ambiguous**: Kafka orders the use-case topic against nothing, so a use case whose
            # row has not arrived yet looks exactly like one that was deleted. Clearing on sight
            # would strip the payloads of traffic that is a second old.
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

            # **The pass leaves a row, and that is the whole of `FRD-608` §2.4.**
            #
            # These two figures have been returned since this module was written and read by
            # nothing: they went into the log line below and out of reach. *"Prompts are deleted
            # after N days"* is a claim a configuration screen can make; *"the last pass ran at
            # 03:00 and cleared 1 412 payloads"* is the one an auditor asks for, and the register
            # cannot print it unless somebody keeps it.
            #
            # Written inside the same transaction as the deletions it describes: a record of an
            # erasure that could commit while the erasure rolled back would be worse than no
            # record, because somebody would believe it.
            #
            # Stamped with `now` rather than a column default, so the moment on the row is the same
            # moment the sweep used to decide what had expired — the two are identical in
            # production and only one of them is testable.
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

        **Retired use cases are in here, and that is the point** (`FRD-607`). Their rows survive as
        tombstones, so the promise this installation made about their prompts — *kept N days* —
        goes on being honoured after somebody presses Delete. While the row was removed, those rows
        fell through to the pass below and followed the *installation default* instead: a different
        promise, substituted silently, at the moment a use case was deleted. Whether that default
        is longer or shorter than the use case's own period is luck, and both directions are wrong.

        A retired use case is **not** swept early either. Retirement is not consent withdrawn; the
        period the data subject was told about is the period that applies.
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

        Only rows that still have a payload are touched, so repeated runs are cheap and the
        reported count is the number actually cleared rather than the number matched.

        ``unknown`` widens the ``use_case is None`` pass to cover every slug **outside** that set —
        the use cases the read-model does not name, which is what a deleted one becomes.
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
    from aira_gateway.config import GatewaySettings, configure_worker
    from aira_gateway.db.base import build_engine, build_sessionmaker

    settings = GatewaySettings()
    # The same reason the consumer does it: a background process that configures nothing has no
    # tracer provider and no structured logging, and an erasure nobody can see afterwards is the
    # one thing this sweep must not be (`FRD-615`).
    configure_worker(settings)
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
