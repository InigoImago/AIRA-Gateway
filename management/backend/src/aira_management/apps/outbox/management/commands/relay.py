"""``relay`` command: publish unsent outbox events to Kafka (FRD-204).

Fetches pending rows (sync), publishes them (async), then marks them published. At-least-once:
a crash after publish but before marking re-publishes on the next run — the consumer is idempotent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from aira_common.kafka import AiokafkaProducer, KafkaRecord, Producer
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.config.runtime import get_settings


def build_producer() -> Producer:
    settings = get_settings()
    return AiokafkaProducer(settings.kafka_bootstrap_servers, settings.kafka_security())


async def _publish(producer: Producer, pending: list[OutboxEvent]) -> list[int]:
    await producer.start()
    published_ids: list[int] = []
    try:
        for event in pending:
            await producer.send(
                KafkaRecord(
                    topic=event.topic,
                    key=event.key,
                    event_type=event.event_type,
                    payload=event.payload,
                    # The context of the request that caused it, not this process's (which has
                    # none). Without it the message carries no trace at all — see the column.
                    traceparent=event.traceparent,
                )
            )
            published_ids.append(event.pk)
    finally:
        await producer.stop()
    return published_ids


#: How many events one run publishes.
#:
#: The query had no bound, so a relay coming back after an outage — a Kafka restart, a broker
#: rebalance, a container that could not start for an hour — loaded **every** pending row into
#: memory at once, each carrying a full JSON payload. That is the failure this project already
#: reasoned about for the audit queue (`RequestLogWriter`: *"An unbounded one would only move the
#: exhaustion it is meant to prevent from the connection pool to memory"*), on the other plane and
#: without the reasoning.
#:
#: A backlog is not lost by this: the relay runs on a loop (`docker-compose.apps.yml`, and a
#: CronJob in Kubernetes), so what does not fit goes out on the next pass, in order. The ordering
#: is `Meta.ordering` — `(created_at, id)` — which is why taking a prefix is safe: an outbox's
#: order is its meaning, and a slice of an ordered queryset is the oldest events, never a sample.
BATCH = 500


class Command(BaseCommand):
    help = "Publish unsent outbox events to Kafka."

    def handle(self, *args: Any, **options: Any) -> None:
        pending = list(OutboxEvent.objects.filter(published_at__isnull=True)[:BATCH])
        if not pending:
            self.stdout.write("no pending events")
            return
        published_ids = asyncio.run(_publish(build_producer(), pending))
        OutboxEvent.objects.filter(pk__in=published_ids).update(published_at=timezone.now())
        remaining = OutboxEvent.objects.filter(published_at__isnull=True).count()
        # Said out loud, because a bounded run that stays silent about what it left is the "silent
        # cap" this project refuses elsewhere: a reader would see "published 500 events" and
        # conclude the outbox is empty.
        note = f" ({remaining} still pending)" if remaining else ""
        self.stdout.write(self.style.SUCCESS(f"published {len(published_ids)} events{note}"))
