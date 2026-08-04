"""Gateway config-consumer worker (FRD-204).

Consumes the config topics and applies each event into the read-model via
:func:`apply_event`. The aiokafka I/O is integration-tested (``# pragma: no cover``);
``decode_event_type`` is unit-tested.
"""

from __future__ import annotations

import asyncio
import sys

from aira_common.kafka import EVENT_TYPE_HEADER, MEMBERSHIP_TOPIC, USECASE_TOPIC
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


def decode_event_type(headers: list[tuple[str, bytes]] | None) -> str | None:
    """Extract the ``event_type`` header from a Kafka message."""
    for key, value in headers or []:
        if key == EVENT_TYPE_HEADER:
            return value.decode("utf-8")
    return None


async def run_consumer(settings: GatewaySettings) -> None:  # pragma: no cover
    from aiokafka import AIOKafkaConsumer

    engine = build_engine(settings.database_url(use_sqlite=False))
    await create_all(engine)
    sessionmaker = build_sessionmaker(engine)

    consumer = AIOKafkaConsumer(
        USECASE_TOPIC,
        MEMBERSHIP_TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="aira-gateway",
        auto_offset_reset="earliest",
        value_deserializer=lambda value: __import__("json").loads(value.decode("utf-8")),
    )
    await consumer.start()
    try:
        async for message in consumer:
            event_type = decode_event_type(message.headers)
            if event_type is None:
                continue
            async with sessionmaker() as session:
                await apply_event(session, event_type, message.value)
    finally:
        await consumer.stop()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    asyncio.run(run_consumer(GatewaySettings()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
