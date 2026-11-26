"""``relay`` command: publish unsent outbox events to Kafka (FRD-204).

Fetches pending rows (sync), publishes them (async), then marks them published. At-least-once:
a crash after publish but before marking re-publishes on the next run — the consumer is idempotent.

- **One run at a time.** Two overlapping runs can each hold an event for the same key and publish
  them in the wrong order, and on a compacted topic the older one is then what survives — a revoked
  group grant granted again. A run takes an advisory lock for its whole length and leaves without
  touching anything when another run holds it. Session-level, so the database connection must not
  sit behind a transaction-pooling proxy.
- **What was sent is marked, whatever happens after.** A send that fails stops the run there —
  order is meaning, so nothing behind it goes first — and every event sent before it is still
  marked, so the next run does not send them again. The run then fails, saying where it stopped.
- **An event the broker will never accept is parked, not retried.** Retried at the head of the
  queue it would hold back every later event of the installation for good. It is set aside with
  its reason, said on every run while it stays, and never sent: the next change to the same thing
  emits a new event.
- **What has been delivered is kept for `KEPT_AFTER_DAYS`, then deleted.** A published row is a
  copy of something the gateway already has, with names in it; it is kept long enough to compare
  what was sent with what arrived. A parked one is kept as long, counted from when it was parked.
- **With `--loop` it runs again after every pause, until SIGTERM** (`FRD-153`), and a run that
  finished — published, found nothing pending, or left the outbox to the run holding it — is a
  heartbeat its container's healthcheck reads. A run that failed is said, and is none.
- **What a run did is one structured line** (`FRD-631` FR-2): `relay_run` with what it published,
  parked and deleted and what is left, through the same pipeline and export as every other line.
  A run that found nothing is said at `debug` — every ten seconds, it would be most of the log. An
  event set aside is a warning when it is set aside, and again at most once an hour while any is
  left, rather than every ten seconds for thirty days.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import Error as DatabaseError
from django.db import connection, connections
from django.utils import timezone

from aira_common.counts import display_count
from aira_common.heartbeat import Heartbeat
from aira_common.kafka import (
    MAX_EVENT_BYTES,
    AiokafkaProducer,
    KafkaRecord,
    Producer,
    RecordRefused,
    encode_event,
)
from aira_common.logging import get_logger
from aira_common.logging.lifecycle import say_stopped
from aira_common.observability import flush_telemetry
from aira_common.periodic import run_every
from aira_management.apps.outbox.models import KEPT_AFTER_DAYS, OutboxEvent
from aira_management.config.runtime import get_settings

#: How many events one run publishes. Bounded so a relay returning after an outage does not load
#: every pending row into memory at once. Nothing is lost: the relay runs on a loop, and a prefix
#: of the `(created_at, id)`-ordered queryset is always the oldest events, in order.
BATCH = 500

#: The advisory lock one relay run holds. Any fixed number; this one spells "AIRA".
RELAY_LOCK = 0x41495241

#: The pause between two runs of `relay --loop`, unless `--interval` names another.
DEFAULT_INTERVAL_SECONDS = 10.0
#: The least age a looping relay's heartbeat may reach: a broker or database restarting fails a few
#: runs in a row, and that is not yet a relay that has stopped.
LEAST_MAX_AGE_SECONDS = 120.0
#: How a run fails in a way its message says all about — a run that stopped at an event, a database
#: that did not answer. Anything else is logged with its traceback, as a run on its own prints it.
RUN_FAILURES = (CommandError, DatabaseError)

#: How often a relay that has set events aside says so again while they stay.
PARKED_REMINDER_SECONDS = 3600.0

_log = get_logger("aira_management.relay")

#: When this process last warned about the events set aside, on the monotonic clock.
_reminded_at: float | None = None


def max_age_for(interval: float) -> float:
    """How old a looping relay's heartbeat may grow: five pauses, and never under two minutes."""
    return max(5 * interval, LEAST_MAX_AGE_SECONDS)


def build_producer() -> Producer:
    settings = get_settings()
    return AiokafkaProducer(settings.kafka_bootstrap_servers, settings.kafka_security())


@dataclass
class Run:
    """What one run did: sent, set aside, and where it stopped if it stopped."""

    published: list[int] = field(default_factory=list)
    parked: dict[int, str] = field(default_factory=dict)
    stopped_at: OutboxEvent | None = None
    failure: BaseException | None = None


async def _publish(producer: Producer, pending: list[OutboxEvent]) -> Run:
    run = Run()
    await producer.start()
    try:
        for event in pending:
            size = len(encode_event(event.payload))
            if size > MAX_EVENT_BYTES:
                run.parked[event.pk] = (
                    f"{display_count(size)} bytes once encoded; the event channel carries at "
                    f"most {display_count(MAX_EVENT_BYTES)}."
                )
                continue
            try:
                await producer.send(
                    KafkaRecord(
                        topic=event.topic,
                        key=event.key,
                        event_type=event.event_type,
                        payload=event.payload,
                        # The context of the request that caused it; this process has none.
                        traceparent=event.traceparent,
                    )
                )
            except RecordRefused as exc:
                run.parked[event.pk] = f"The broker refused it for good: {exc}"
                continue
            except Exception as exc:
                run.stopped_at, run.failure = event, exc
                break
            run.published.append(event.pk)
    finally:
        try:
            await producer.stop()
        except Exception as exc:
            # Too late to matter to what was sent, which is marked all the same.
            run.failure = run.failure or exc
    return run


@contextmanager
def exclusive_run() -> Iterator[bool]:
    """Hold the relay's lock for the duration, yielding whether it was free.

    PostgreSQL's advisory lock; any other database is the test suite's in-memory one, which no
    second process can reach.
    """
    if connection.vendor != "postgresql":
        yield True
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [RELAY_LOCK])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [RELAY_LOCK])


def sweep(now: Any) -> int:
    """Delete what was delivered or parked more than `KEPT_AFTER_DAYS` ago. How many went."""
    cutoff = now - timedelta(days=KEPT_AFTER_DAYS)
    deleted, _by_model = OutboxEvent.objects.filter(published_at__lt=cutoff).delete()
    parked, _by_model = OutboxEvent.objects.filter(parked_at__lt=cutoff).delete()
    return deleted + parked


def sweep_history(now: Any) -> int:
    """Delete configuration changes older than `AIRA_CONFIG_HISTORY_DAYS` (`FRD-632`). How many.

    Here because the relay is the process that already keeps Management's tables to their periods,
    and runs whether or not anybody opens the console.
    """
    from aira_management.apps.history.models import ConfigurationChange

    cutoff = now - timedelta(days=get_settings().config_history_days)
    deleted, _by_model = ConfigurationChange.objects.filter(changed_at__lt=cutoff).delete()
    return int(deleted)


class Command(BaseCommand):
    help = "Publish unsent outbox events to Kafka: once, or again after every pause with --loop."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run again after every pause until SIGTERM, and keep the heartbeat the "
            "container's healthcheck reads.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=None,
            help=f"Seconds between two runs with --loop (default {DEFAULT_INTERVAL_SECONDS:g}).",
        )
        parser.add_argument(
            "--max-age",
            type=float,
            default=None,
            dest="max_age",
            help="Seconds without a finished run before the container reads unhealthy, with "
            f"--loop (default five intervals, at least {LEAST_MAX_AGE_SECONDS:g}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        interval, max_age = options["interval"], options["max_age"]
        if not options["loop"]:
            if interval is not None or max_age is not None:
                raise CommandError("--interval and --max-age apply to --loop")
            self._run()
            return
        interval = DEFAULT_INTERVAL_SECONDS if interval is None else interval
        max_age = max_age_for(interval) if max_age is None else max_age
        try:
            heartbeat = Heartbeat("relay", max_age, log=_log)
            run_every(
                interval, self._run_in_loop, heartbeat=heartbeat, log=_log, expected=RUN_FAILURES
            )
        except ValueError as exc:
            # What the loop refuses before its first run: a pause or an age that cannot work.
            raise CommandError(str(exc)) from exc
        # Stopped: the last lines, `worker_stopped` among them, handed over before the process
        # ends rather than left in the export's batch (`FRD-631` FR-4).
        say_stopped(service=get_settings().app_name)
        flush_telemetry()

    def _run_in_loop(self) -> None:
        try:
            self._run()
        finally:
            # **A run's connection is not the next run's.** One the database closed — a restart, a
            # failover — fails every query on it, so a loop that kept it would fail every run after
            # it; the run-per-process this loop replaces could not.
            connections.close_all()

    def _run(self) -> None:
        with exclusive_run() as acquired:
            if not acquired:
                _log.info("relay_skipped", reason="another relay run holds the outbox")
                return
            self._relay()

    def _relay(self) -> None:
        moment = timezone.now()
        swept = sweep(moment)
        expired = sweep_history(moment)
        if expired:
            _log.info("config_history_pruned", rows_deleted=expired)
        pending = list(OutboxEvent.objects.pending()[:BATCH])
        if not pending:
            still_parked = self._say_what_is_parked()
            # Every ten seconds on an idle installation: at `debug`, or it is most of the log.
            say = _log.info if swept else _log.debug
            say("relay_run", published=0, pending=0, swept=swept, still_parked=still_parked)
            return
        run = asyncio.run(_publish(build_producer(), pending))
        moment = timezone.now()
        OutboxEvent.objects.filter(pk__in=run.published).update(published_at=moment)
        for pk, reason in run.parked.items():
            OutboxEvent.objects.filter(pk=pk).update(parked_at=moment, parked_reason=reason)
            event = next(event for event in pending if event.pk == pk)
            # Once, when it happens: a change set aside is a change the gateway never saw.
            _log.warning(
                "relay_event_parked",
                event_id=pk,
                event_type=event.event_type,
                topic=event.topic,
                reason=reason,
            )
        remaining = OutboxEvent.objects.pending().count()
        still_parked = self._say_what_is_parked(reminded=bool(run.parked))
        # `pending` said out loud, so "published 500" is not read as "the outbox is empty".
        _log.info(
            "relay_run",
            published=len(run.published),
            parked=len(run.parked),
            pending=remaining,
            swept=swept,
            still_parked=still_parked,
        )
        if run.stopped_at is not None:
            raise CommandError(
                f"publishing stopped at event {run.stopped_at.pk} ({run.stopped_at.event_type} "
                f"on {run.stopped_at.topic}): {run.failure!r}. It and every event after it stay "
                "pending for the next run."
            )
        if run.failure is not None:
            raise CommandError(f"the producer did not stop cleanly: {run.failure!r}")

    def _say_what_is_parked(self, *, reminded: bool = False) -> int:
        """How many events stay set aside — and, at most once an hour while any do, a warning
        naming the oldest: an event set aside is a change the gateway never saw. ``reminded`` is
        a run that has just warned about the ones it set aside."""
        parked = OutboxEvent.objects.filter(parked_at__isnull=False).order_by("parked_at", "id")
        count = parked.count()
        global _reminded_at
        now = time.monotonic()
        if reminded:
            _reminded_at = now
        elif count and (_reminded_at is None or now - _reminded_at >= PARKED_REMINDER_SECONDS):
            oldest = parked.first()
            _reminded_at = now
            _log.warning(
                "relay_events_still_parked",
                count=count,
                event_id=oldest.pk if oldest else None,
                event_type=oldest.event_type if oldest else None,
                topic=oldest.topic if oldest else None,
                reason=oldest.parked_reason if oldest else None,
            )
        return count
