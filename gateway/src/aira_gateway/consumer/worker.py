"""Gateway configuration-consumer worker (`FRD-204`).

Consumes the configuration topics and applies each event into the read-model via
:func:`apply_event`. Client construction is integration-tested (``# pragma: no cover``); the loop
and the per-message handling are unit-tested against a fake broker.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.integration_debug import watch
from aira_common.kafka import (
    CONFIG_TOPICS,
    CONSUMER_GROUP,
    EVENT_TYPE_HEADER,
    consumer_group,
    staged,
)
from aira_common.logging import get_logger
from aira_common.observability import consuming
from aira_gateway.config import GatewaySettings, configure_worker
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import build_engine, build_sessionmaker

_log = get_logger("aira_gateway.consumer")

#: The consumer group without a stage, named once so the `FRD-617` diagnostic reports the group
#: that subscribed.
GROUP_ID = CONSUMER_GROUP


def subscriptions(stage: str) -> tuple[str, ...]:
    """Every configuration topic, for ``stage`` — the same names Management publishes to."""
    return tuple(staged(topic, stage) for topic in CONFIG_TOPICS)


def decode_event_type(headers: list[tuple[str, bytes]] | None) -> str | None:
    """Extract the ``event_type`` header from a Kafka message."""
    for key, value in headers or []:
        if key == EVENT_TYPE_HEADER:
            return value.decode("utf-8")
    return None


async def apply_one_message(
    sessionmaker: async_sessionmaker[AsyncSession], message: Any
) -> str | None:
    """Apply one configuration event, and **survive it if it cannot be applied**.

    A failure that escaped here would stop the consumer, and the restarted container would read the
    same message and die again — a poison pill that silently freezes the read-model, so a revoked
    key goes on working. One event that cannot be applied is therefore skipped, **loudly**, with
    the topic, partition and offset somebody needs to go and look at it.

    Returns the event type it applied, or ``None`` if it skipped.
    """
    event_type = decode_event_type(message.headers)
    if event_type is None:
        # A message without a type header on a topic only Management publishes to means somebody
        # else is producing to it — worth a line, not a silent skip.
        _log.warning(
            "config_event_without_type",
            topic=getattr(message, "topic", None),
            partition=getattr(message, "partition", None),
            offset=getattr(message, "offset", None),
        )
        return None
    topic = getattr(message, "topic", None)
    # Continue the producer's trace from the message's `traceparent`, so a configuration change is
    # one trace across both planes (`FRD-615`). A no-op when observability is off.
    with consuming(
        str(topic or "aira"),
        message.headers,
        {
            "aira.event_type": event_type,
            "messaging.kafka.offset": getattr(message, "offset", None),
            "messaging.kafka.partition": getattr(message, "partition", None),
        },
    ) as processing:
        try:
            async with sessionmaker() as session:
                await apply_event(session, event_type, message.value)
        except Exception as exc:  # noqa: BLE001 — see the docstring: never take the consumer down
            # Marked on the span as well: the `except` keeps the failure from reaching it on its
            # own, and a green trace beside a red log is read as the green one.
            processing.failed(exc)
            _log.error(
                "config_event_failed",
                event_type=event_type,
                topic=topic,
                partition=getattr(message, "partition", None),
                offset=getattr(message, "offset", None),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
    return event_type


async def consume_forever(
    consumer: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
    target: str = "",
    group: str = GROUP_ID,
) -> int:
    """Start the consumer, apply everything that arrives, and stop it. Returns messages seen.

    Split from `run_consumer` so the `FRD-617` call sites — `consumer.start()`, where SASL, trust
    store and broker address are first proven, and the per-message receive line — can be driven
    with a fake broker. ``target`` is the broker address, passed in rather than read off a private
    attribute of the client.
    """
    with watch("kafka", "consumer.start", target=target, group=group):
        await consumer.start()
    seen = 0
    try:
        async for message in consumer:
            seen += 1
            # `apply_one_message` swallows its own failures, so `applied` carries the difference.
            with watch(
                "kafka",
                "consumer.receive",
                topic=getattr(message, "topic", None),
                partition=getattr(message, "partition", None),
                offset=getattr(message, "offset", None),
            ) as call:
                applied = await apply_one_message(sessionmaker, message)
                call.note(event_type=applied, applied=applied is not None)
        return seen
    finally:
        await consumer.stop()


async def run_consumer(settings: GatewaySettings) -> None:  # pragma: no cover
    from aiokafka import AIOKafkaConsumer

    # Before the first message, or the spans `apply_one_message` opens have no tracer (`FRD-615`).
    configure_worker(settings)

    engine = build_engine(settings.database_url(use_sqlite=False))
    # **No `create_all` here.** `gateway-migrate` runs the migrations first, and `create_all` beside
    # Alembic lets an older container resurrect a table a migration dropped (`FRD-114`).
    sessionmaker = build_sessionmaker(engine)

    group = consumer_group(settings.kafka_stage)
    consumer = AIOKafkaConsumer(
        *subscriptions(settings.kafka_stage),
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group,
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        # Authenticated like the relay that publishes: an open broker is a way to write straight
        # into the read-model this gateway's authorization is read from (`KafkaSecurity`).
        **settings.kafka_security().client_kwargs(),
    )
    try:
        await consume_forever(consumer, sessionmaker, settings.kafka_bootstrap_servers, group)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    asyncio.run(run_consumer(GatewaySettings()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
