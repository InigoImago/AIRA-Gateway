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
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from aira_common.kafka import (
    MAX_EVENT_BYTES,
    AiokafkaProducer,
    KafkaRecord,
    Producer,
    RecordRefused,
    encode_event,
)
from aira_management.apps.outbox.models import KEPT_AFTER_DAYS, OutboxEvent
from aira_management.config.runtime import get_settings

#: How many events one run publishes. Bounded so a relay returning after an outage does not load
#: every pending row into memory at once. Nothing is lost: the relay runs on a loop, and a prefix
#: of the `(created_at, id)`-ordered queryset is always the oldest events, in order.
BATCH = 500

#: The advisory lock one relay run holds. Any fixed number; this one spells "AIRA".
RELAY_LOCK = 0x41495241


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
                    f"{size:,} bytes once encoded; the event channel carries at most "
                    f"{MAX_EVENT_BYTES:,}."
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


class Command(BaseCommand):
    help = "Publish unsent outbox events to Kafka."

    def handle(self, *args: Any, **options: Any) -> None:
        with exclusive_run() as acquired:
            if not acquired:
                self.stdout.write("another relay run holds the outbox; nothing done")
                return
            self._relay()

    def _relay(self) -> None:
        swept = sweep(timezone.now())
        if swept:
            self.stdout.write(f"deleted {swept} events kept past {KEPT_AFTER_DAYS} days")
        pending = list(OutboxEvent.objects.pending()[:BATCH])
        if not pending:
            self.stdout.write("no pending events")
            self._say_what_is_parked()
            return
        run = asyncio.run(_publish(build_producer(), pending))
        moment = timezone.now()
        OutboxEvent.objects.filter(pk__in=run.published).update(published_at=moment)
        for pk, reason in run.parked.items():
            OutboxEvent.objects.filter(pk=pk).update(parked_at=moment, parked_reason=reason)
        remaining = OutboxEvent.objects.pending().count()
        # Said out loud, so "published 500 events" is not read as "the outbox is empty".
        note = f" ({remaining} still pending)" if remaining else ""
        self.stdout.write(self.style.SUCCESS(f"published {len(run.published)} events{note}"))
        self._say_what_is_parked()
        if run.stopped_at is not None:
            raise CommandError(
                f"publishing stopped at event {run.stopped_at.pk} ({run.stopped_at.event_type} "
                f"on {run.stopped_at.topic}): {run.failure!r}. It and every event after it stay "
                "pending for the next run."
            )
        if run.failure is not None:
            raise CommandError(f"the producer did not stop cleanly: {run.failure!r}")

    def _say_what_is_parked(self) -> None:
        """Every run, while any remain: an event set aside is a change the gateway never saw."""
        parked = OutboxEvent.objects.filter(parked_at__isnull=False).order_by("parked_at", "id")
        for event in parked:
            self.stderr.write(
                self.style.WARNING(
                    f"parked: event {event.pk} ({event.event_type} {event.key!r} on "
                    f"{event.topic}) — {event.parked_reason}"
                )
            )
