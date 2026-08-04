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
    return AiokafkaProducer(get_settings().kafka_bootstrap_servers)


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
                )
            )
            published_ids.append(event.pk)
    finally:
        await producer.stop()
    return published_ids


class Command(BaseCommand):
    help = "Publish unsent outbox events to Kafka."

    def handle(self, *args: Any, **options: Any) -> None:
        pending = list(OutboxEvent.objects.filter(published_at__isnull=True))
        if not pending:
            self.stdout.write("no pending events")
            return
        published_ids = asyncio.run(_publish(build_producer(), pending))
        OutboxEvent.objects.filter(pk__in=published_ids).update(published_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"published {len(published_ids)} events"))
