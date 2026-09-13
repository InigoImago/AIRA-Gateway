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

#: How many events one run publishes. Bounded so a relay returning after an outage does not load
#: every pending row into memory at once. Nothing is lost: the relay runs on a loop, and a prefix
#: of the `(created_at, id)`-ordered queryset is always the oldest events, in order.
BATCH = 500


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
                    # The context of the request that caused it; this process has none.
                    traceparent=event.traceparent,
                )
            )
            published_ids.append(event.pk)
    finally:
        await producer.stop()
    return published_ids


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
        # Said out loud, so "published 500 events" is not read as "the outbox is empty".
        note = f" ({remaining} still pending)" if remaining else ""
        self.stdout.write(self.style.SUCCESS(f"published {len(published_ids)} events{note}"))
