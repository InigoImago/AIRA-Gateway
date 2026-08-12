"""Gateway config-consumer worker (FRD-204).

Consumes the config topics and applies each event into the read-model via
:func:`apply_event`. The aiokafka I/O is integration-tested (``# pragma: no cover``);
``decode_event_type`` is unit-tested.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.kafka import (
    ANOMALY_RULE_TOPIC,
    API_KEY_TOPIC,
    BUDGET_TOPIC,
    EVENT_TYPE_HEADER,
    MEMBERSHIP_TOPIC,
    MODEL_TOPIC,
    PIPELINE_TOPIC,
    RATE_LIMIT_TOPIC,
    USECASE_TOPIC,
)
from aira_common.logging import get_logger
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import build_engine, build_sessionmaker

_log = get_logger("aira_gateway.consumer")


def decode_event_type(headers: list[tuple[str, bytes]] | None) -> str | None:
    """Extract the ``event_type`` header from a Kafka message."""
    for key, value in headers or []:
        if key == EVENT_TYPE_HEADER:
            return value.decode("utf-8")
    return None


async def apply_one_message(
    sessionmaker: async_sessionmaker[AsyncSession], message: Any
) -> str | None:
    """Apply one config event, and **survive it if it cannot be applied**.

    The loop used to call `apply_event` directly, so any failure propagated out of `async for`,
    stopped the consumer and ended the process. Every handler indexes its payload
    (`payload["slug"]`, `payload["prefix"]`, …), so one malformed event — a field renamed by a
    newer Management, a truncated value, a transient database error — was enough.

    What follows is worse than the crash. The container restarts, reads the same message and dies
    again: a **poison pill**, and config distribution is off for everything behind it while nothing
    says so. The gateway keeps serving happily from a read-model that has quietly stopped being
    updated, so a *revoked API key goes on working*, a new budget never arrives, and a use case
    somebody deleted still answers. And because offsets are auto-committed on a timer, the other
    outcome is available too: the offset moves past the bad message first and the event is lost
    without a trace. Which of the two happens is a race.

    So one event that cannot be applied is now one event that is skipped — **loudly**, with the
    topic, partition and offset, which is what somebody needs to go and look at it. The alternative
    would be to stop the world for every bad row, and a control plane that stops distributing
    revocations because one unrelated event was malformed is not the safer of the two.

    Returns the event type it applied, or ``None`` if it skipped, so a caller can count.
    """
    event_type = decode_event_type(message.headers)
    if event_type is None:
        # Named rather than silently skipped: this repository has now found three defects whose
        # whole mechanism was a `return` for something unrecognised, and a message with no type
        # header on a topic only we publish to means somebody is producing to it — worth a line.
        _log.warning(
            "config_event_without_type",
            topic=getattr(message, "topic", None),
            partition=getattr(message, "partition", None),
            offset=getattr(message, "offset", None),
        )
        return None
    try:
        async with sessionmaker() as session:
            await apply_event(session, event_type, message.value)
    except Exception as exc:  # noqa: BLE001 — see the docstring: never take the consumer down
        _log.error(
            "config_event_failed",
            event_type=event_type,
            topic=getattr(message, "topic", None),
            partition=getattr(message, "partition", None),
            offset=getattr(message, "offset", None),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None
    return event_type


async def _apply_one(  # pragma: no cover - a thin alias over the tested function
    sessionmaker: async_sessionmaker[AsyncSession], message: Any
) -> None:
    await apply_one_message(sessionmaker, message)


async def run_consumer(settings: GatewaySettings) -> None:  # pragma: no cover
    from aiokafka import AIOKafkaConsumer

    engine = build_engine(settings.database_url(use_sqlite=False))
    # **No `create_all` here.** `gateway-migrate` runs `alembic upgrade head` and this worker waits
    # for it, so the schema already exists — and `FRD-114` recorded what the belt-and-braces call
    # actually did: an older container resurrected a table a migration had dropped and then failed
    # every event against it. `create_all` beside Alembic lets a partially-deployed stack undo a
    # migration, which is a worse failure than the missing table it was insuring against.
    sessionmaker = build_sessionmaker(engine)

    consumer = AIOKafkaConsumer(
        USECASE_TOPIC,
        MEMBERSHIP_TOPIC,
        API_KEY_TOPIC,
        PIPELINE_TOPIC,
        BUDGET_TOPIC,
        RATE_LIMIT_TOPIC,
        ANOMALY_RULE_TOPIC,
        MODEL_TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="aira-gateway",
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        # The consumer authenticates with the same settings the relay publishes under. An
        # unauthenticated broker is a way to write straight into the read-model this gateway's
        # authorization is read from — see `KafkaSecurity`.
        **settings.kafka_security().client_kwargs(),
    )
    await consumer.start()
    try:
        async for message in consumer:
            await _apply_one(sessionmaker, message)
    finally:
        await consumer.stop()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    asyncio.run(run_consumer(GatewaySettings()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
