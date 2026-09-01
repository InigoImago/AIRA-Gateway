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

from aira_common.integration_debug import watch
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
from aira_common.observability import consuming
from aira_gateway.config import GatewaySettings, configure_worker
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import build_engine, build_sessionmaker

_log = get_logger("aira_gateway.consumer")

#: The consumer group both the client and the `FRD-617` line name. One constant, because a
#: diagnostic that reports a different group than the one that subscribed is a diagnostic that
#: sends somebody to look at the wrong consumer lag.
GROUP_ID = "aira-gateway"


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
    topic = getattr(message, "topic", None)
    # **The far end of the trace.** The producer has put a `traceparent` on every message since
    # `FRD-001`; nothing here ever read it, so the work this event causes belonged to no trace and
    # the two planes could not be followed through one (`FRD-615`). A no-op when observability is
    # off.
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
            # Marked on the span as well as logged. The `except` is what keeps one bad event from
            # taking the consumer down, and it is also what stops the failure reaching the span on
            # its own — a trace that stays green while the log says otherwise is worse than no
            # trace, because somebody reads the green one.
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
    consumer: Any, sessionmaker: async_sessionmaker[AsyncSession], target: str = ""
) -> int:
    """Start the consumer, apply everything that arrives, and stop it. Returns messages seen.

    Split out of `run_consumer` so the loop can be driven with a fake broker. What is being tested
    is not the `async for` — it is that the three `FRD-617` call sites exist and are reached:
    `consumer.start()` is where a SASL mechanism, a trust store and a broker address are first
    proven against reality, and the receive line is the only place that says *an event arrived at
    all*. `run_consumer` around this is client construction and nothing else.

    ``target`` is the broker address, carried for the line rather than read back off the client —
    aiokafka spells it differently in different versions, and a diagnostic that guesses at a
    private attribute is one that quietly starts saying `None`.
    """
    with watch("kafka", "consumer.start", target=target, group=GROUP_ID):
        await consumer.start()
    seen = 0
    try:
        async for message in consumer:
            seen += 1
            # `apply_one_message` swallows its own failures on purpose — one bad event must not
            # take the consumer down — so this `watch` will almost never see an exception. The
            # `applied` field is what carries the difference; the failure itself is already a
            # `config_event_failed` line and a red span.
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

    # **Before the first message.** Without this the process has no tracer provider, so the
    # span `apply_one_message` opens is discarded before it is built and a configuration
    # change stops being one trace at the bus after all (`FRD-615`).
    configure_worker(settings)

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
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        # The consumer authenticates with the same settings the relay publishes under. An
        # unauthenticated broker is a way to write straight into the read-model this gateway's
        # authorization is read from — see `KafkaSecurity`.
        **settings.kafka_security().client_kwargs(),
    )
    try:
        await consume_forever(consumer, sessionmaker, settings.kafka_bootstrap_servers)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    asyncio.run(run_consumer(GatewaySettings()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
