"""Retention for stored request payloads (FRD-404).

``request_logs`` keeps what a caller sent and what the model answered — the most useful material
for an incident and the most sensitive thing AIRA holds. Two clocks, deliberately separate:

- **Payload retention** — per use case, default one week. After it the bodies are removed and the
  row keeps its metadata, which the spend and usage reporting reads.
- **Record retention** — installation-wide, off by default (``AIRA_LOG_RETENTION_DAYS``). When set,
  whole rows older than that are removed.

Run it periodically: ``python -m aira_gateway.retention`` sweeps once, for a scheduler, and
``python -m aira_gateway.retention --loop`` sweeps again after every pause until SIGTERM, keeping
the heartbeat its container's healthcheck reads (`FRD-153`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.heartbeat import Heartbeat
from aira_common.logging import get_logger, process_named
from aira_common.logging.lifecycle import say_stopped
from aira_common.observability import flush_telemetry
from aira_common.periodic import Wait, run_every
from aira_gateway.db.errors import loggable
from aira_gateway.db.models import RequestLog, RetentionRun, UseCaseRead

if TYPE_CHECKING:
    from aira_gateway.config import GatewaySettings

DEFAULT_RETENTION_DAYS = 7

#: The pause between two sweeps of ``--loop``, unless ``--interval`` names another.
DEFAULT_INTERVAL_SECONDS = 3600.0
#: What a sweep may take, on top of two pauses, before the heartbeat of a looping sweep is stale:
#: one sweep that fails is not yet a sweep that has stopped, and two in a row are.
SWEEP_ALLOWANCE_SECONDS = 600.0

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


def max_age_for(interval: float) -> float:
    """How old a looping sweep's heartbeat may grow: two pauses, and the time a sweep takes."""
    return 2 * interval + SWEEP_ALLOWANCE_SECONDS


class SweepFailed(Exception):
    """A sweep that did not finish, said without the values its statement was about."""


Prune = Callable[[], Awaitable[PruneResult]]


def sweep_once(prune: Prune) -> None:
    """One sweep. Its outcome is the line `RetentionService.prune` writes, `retention_pruned`, and
    nothing beside it on stdout (`FRD-631` FR-2). A database's failure is raised as
    :class:`SweepFailed`, its message cut as every database error this plane logs is
    (:func:`loggable`)."""
    try:
        asyncio.run(prune())
    except SQLAlchemyError as exc:
        raise SweepFailed(f"{type(exc).__name__}: {loggable(exc)}") from exc


def sweep_forever(
    prune: Prune,
    *,
    interval: float | None = None,
    max_age: float | None = None,
    stop: threading.Event | None = None,
    wait: Wait | None = None,
) -> None:
    """Sweep now and again after every pause until SIGTERM; a finished sweep is a heartbeat."""
    pause = DEFAULT_INTERVAL_SECONDS if interval is None else interval
    heartbeat = Heartbeat("retention", max_age_for(pause) if max_age is None else max_age, log=_log)
    run_every(
        pause,
        lambda: sweep_once(prune),
        heartbeat=heartbeat,
        log=_log,
        expected=(SweepFailed,),
        stop=stop,
        wait=wait,
    )


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m aira_gateway.retention",
        description="Remove stored payloads and rows past their retention: once, or with --loop "
        "again after every pause.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Sweep again after every pause until SIGTERM, and keep the heartbeat the container's "
        "healthcheck reads.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=f"Seconds between two sweeps with --loop (default {DEFAULT_INTERVAL_SECONDS:g}).",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=None,
        dest="max_age",
        help="Seconds without a finished sweep before the container reads unhealthy, with --loop "
        f"(default two intervals and {SWEEP_ALLOWANCE_SECONDS:g}).",
    )
    parsed = parser.parse_args(argv)
    if not parsed.loop and (parsed.interval is not None or parsed.max_age is not None):
        parser.error("--interval and --max-age apply to --loop")
    return parsed


async def _prune(settings: GatewaySettings) -> PruneResult:  # pragma: no cover - process wrapper
    from aira_gateway.db.base import build_engine, build_sessionmaker

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
    from aira_gateway.config import GatewaySettings, configure_process

    parsed = arguments(argv)
    # Named before the settings are read: reading them from Vault writes this process's first lines.
    process_named("retention")
    settings = GatewaySettings()
    # An erasure nobody can see afterwards is the one thing this sweep must not be (`FRD-615`).
    configure_process(settings, process="retention")
    try:
        if not parsed.loop:
            asyncio.run(_prune(settings))
            return 0
        try:
            sweep_forever(
                lambda: _prune(settings), interval=parsed.interval, max_age=parsed.max_age
            )
        except ValueError as exc:
            # What the loop refuses before its first sweep: a pause or an age that cannot work.
            _log.error("retention_not_started", worker="retention", reason=str(exc))
            return 2
        return 0
    finally:
        say_stopped(service=settings.app_name)
        flush_telemetry()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
