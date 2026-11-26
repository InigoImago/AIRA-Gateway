"""Gateway configuration-consumer worker (`FRD-204`).

Consumes the configuration topics and applies each event into the read-model via
:func:`apply_event`, and says how that is going in a heartbeat its container's healthcheck reads
(`FRD-153`). Client construction is integration-tested (``# pragma: no cover``); the loop, the
per-message handling and the heartbeat are unit-tested against a fake broker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from aiokafka.errors import KafkaError
from aiokafka.structs import TopicPartition
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.heartbeat import Heartbeat
from aira_common.integration_debug import watch
from aira_common.kafka import (
    CONFIG_TOPICS,
    CONSUMER_GROUP,
    EVENT_TYPE_HEADER,
    consumer_group,
    staged,
)
from aira_common.logging import get_logger, process_named
from aira_common.logging.lifecycle import say_stopped
from aira_common.observability import consuming, flush_telemetry
from aira_gateway.config import GatewaySettings, configure_worker
from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import build_engine, build_sessionmaker
from aira_gateway.db.errors import loggable
from aira_gateway.db.transient import is_transient

_log = get_logger("aira_gateway.consumer")

#: The consumer group without a stage, named once so the `FRD-617` diagnostic reports the group
#: that subscribed.
GROUP_ID = CONSUMER_GROUP

#: The pause before trying again what failed for a reason that passes — the database restarting or
#: failing over, the broker not up yet. Doubled after each failure from the first figure, and never
#: longer than the second, so a component that comes back is noticed within half a minute however
#: long it was away, and one that is coming back is not hammered meanwhile.
RETRY_FIRST_SECONDS = 1.0
RETRY_LONGEST_SECONDS = 30.0

#: How old the consumer's heartbeat may grow before its container reads unhealthy (`FRD-153`).
MAX_AGE_SECONDS = 90.0
#: How often an idle consumer that is in contact with its broker says so.
IDLE_BEAT_SECONDS = 15.0
#: How long an assigned partition may go without an answer to a fetch before the consumer counts as
#: out of contact with its broker. A broker holds a fetch at most half a second when nothing is new,
#: so a reachable one answers many times a second.
BROKER_SILENCE_SECONDS = 30.0

Sleep = Callable[[float], Awaitable[None]]


def retry_delay(failures: int) -> float:
    """The pause after the ``failures``-th failure in a row (1, 2, …)."""
    # The exponent is held below where the product could overflow a float on a long outage.
    return min(RETRY_LONGEST_SECONDS, RETRY_FIRST_SECONDS * 2.0 ** min(failures - 1, 16))


def subscriptions(stage: str) -> tuple[str, ...]:
    """Every configuration topic, for ``stage`` — the same names Management publishes to."""
    return tuple(staged(topic, stage) for topic in CONFIG_TOPICS)


def decode_event_type(headers: list[tuple[str, bytes | None]] | None) -> str | None:
    """The ``event_type`` header of a Kafka message, or ``None`` if it carries no readable one.

    Headers are bytes anybody with access to the topic chose, so a value that is null or is not
    UTF-8 names no event rather than raising: the caller skips such a message, by name.
    """
    for key, value in headers or []:
        if key == EVENT_TYPE_HEADER:
            if value is None:
                return None
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def event_payload(value: object) -> dict[str, Any]:
    """A message's value as the event payload every handler reads — a JSON object.

    Decoded here rather than by the client's deserializer, which runs before
    :func:`apply_one_message` can contain anything: a value that is not JSON, or a compacted
    topic's tombstone (a null value), raised inside the client and ended the consumer. Raises
    ``ValueError`` for anything but an object, which the caller skips like any unappliable event.
    """
    if isinstance(value, bytes | bytearray):
        value = json.loads(value.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"the message value is {type(value).__name__}, not a JSON object")
    return value


class StoppedWhileHolding(Exception):
    """The consumer was told to stop while it held an event for another attempt.

    The event is not applied and its offset is not committed, so the next consumer reads it again —
    which every handler takes without harm. Waiting for the database to come back instead would be
    waiting past the orchestrator's patience, and end the same way, only killed.
    """


async def pause(seconds: float, *, sleep: Sleep, stop: asyncio.Event | None) -> bool:
    """Wait ``seconds``, or less if ``stop`` is set meanwhile. Returns whether it was."""
    if stop is None:
        await sleep(seconds)
        return False
    if stop.is_set():
        return True
    sleeping = asyncio.ensure_future(sleep(seconds))
    stopping = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({sleeping, stopping}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiting in (sleeping, stopping):
            if not waiting.done():
                waiting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiting
    return stop.is_set()


async def apply_one_message(
    sessionmaker: async_sessionmaker[AsyncSession],
    message: Any,
    *,
    sleep: Sleep = asyncio.sleep,
    stop: asyncio.Event | None = None,
) -> str | None:
    """Apply one configuration event, and **survive it if it cannot be applied**.

    A failure that escaped here would stop the consumer, and the restarted container would read the
    same message and die again — a poison pill that silently freezes the read-model, so a revoked
    key goes on working. One event that cannot be applied is therefore skipped, **loudly**, with
    the topic, partition and offset somebody needs to go and look at it.

    **A failure of the database's is not the event's.** The database restarting or failing over, a
    connection the server closed, a deadlock, a timeout (:func:`is_transient`) fail an event that
    would apply a moment later, and skipping it would lose the change for good: the offset moves
    on, and a revocation that meets a restart leaves the key working while the console shows it
    revoked. Such an event is held and tried again, after a pause that grows to
    :data:`RETRY_LONGEST_SECONDS`, for as long as it takes — nothing behind it on the topic could
    be applied meanwhile either.

    **A stop ends the holding, never an application.** An event being applied is applied and its
    offset committed before the consumer stops; one held for another attempt raises
    :class:`StoppedWhileHolding` when ``stop`` is set, uncommitted.

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
        failures = 0
        while True:
            try:
                payload = event_payload(message.value)
                async with sessionmaker() as session:
                    await apply_event(session, event_type, payload)
            except Exception as exc:  # noqa: BLE001 — see the docstring: never take the consumer down
                if is_transient(exc):
                    failures += 1
                    delay = retry_delay(failures)
                    _log.warning(
                        "config_event_retrying",
                        event_type=event_type,
                        topic=topic,
                        partition=getattr(message, "partition", None),
                        offset=getattr(message, "offset", None),
                        attempt=failures,
                        retry_in_seconds=delay,
                        error=loggable(exc),
                        error_type=type(exc).__name__,
                    )
                    if await pause(delay, sleep=sleep, stop=stop):
                        raise StoppedWhileHolding(event_type) from exc
                    continue
                # Marked on the span as well: the `except` keeps the failure from reaching it on
                # its own, and a green trace beside a red log is read as the green one.
                processing.failed(exc)
                _log.error(
                    "config_event_failed",
                    event_type=event_type,
                    topic=topic,
                    partition=getattr(message, "partition", None),
                    offset=getattr(message, "offset", None),
                    # Never the values: a configuration event carries fields logged by name only.
                    error=loggable(exc),
                    error_type=type(exc).__name__,
                )
                return None
            return event_type


class Health:
    """What the consumer says about itself in its heartbeat (`FRD-153`).

    **A beat is progress**: the consumer started, applied or skipped an event, or is idle with its
    partitions assigned and its broker answering. **Nothing else is**, and no beat is written while
    it waits for a broker at start, holds an event for retry — a database that never comes back
    holds every later event with it — has no partition to read, or hears nothing from the broker it
    is assigned to; after :data:`MAX_AGE_SECONDS` of that its container reads unhealthy.
    """

    def __init__(
        self,
        heartbeat: Heartbeat,
        *,
        clock: Callable[[], float] = time.time,
        tick: Sleep = asyncio.sleep,
    ) -> None:
        self._heartbeat = heartbeat
        self._clock = clock
        self._tick = tick
        self._started = False
        self._applying = False

    def started(self) -> None:
        """The broker accepted the consumer."""
        self._started = True
        self._heartbeat.beat()

    @contextlib.contextmanager
    def applying(self) -> Iterator[None]:
        """An event is in hand — applied, held for retry, or its offset committed."""
        self._applying = True
        try:
            yield
        finally:
            self._applying = False

    def applied(self) -> None:
        """An event was applied or deliberately skipped, and its offset committed or said."""
        self._heartbeat.beat()

    def check(self, consumer: Any) -> None:
        """One look at the consumer between events: a beat, or the reason there is none."""
        reason = self._idle_is_not_progress(consumer)
        if reason is None:
            self._heartbeat.beat()
        else:
            self._heartbeat.missed(reason)

    def _idle_is_not_progress(self, consumer: Any) -> str | None:
        if not self._started:
            return "waiting for Kafka"
        if self._applying:
            return "an event is being applied or held for retry"
        assigned = consumer.assignment()
        if not assigned:
            return "no partition is assigned to it"
        # aiokafka stamps a partition each time its broker answers a fetch for it, events or none,
        # with the wall clock in milliseconds.
        now_ms = self._clock() * 1000.0
        for partition in sorted(assigned):
            answered_ms = consumer.last_poll_timestamp(partition)
            if answered_ms is None or now_ms - answered_ms > BROKER_SILENCE_SECONDS * 1000.0:
                return (
                    f"its broker has not answered a fetch for {partition.topic} "
                    f"partition {partition.partition} in {BROKER_SILENCE_SECONDS:.0f} s"
                )
        return None

    async def watch(self, consumer: Any) -> None:
        """Look every :data:`IDLE_BEAT_SECONDS`, for as long as the consumer runs."""
        while True:
            await self._tick(IDLE_BEAT_SECONDS)
            try:
                self.check(consumer)
            except Exception as exc:  # noqa: BLE001 - a look that fails is not progress, and is said
                _log.warning(
                    "consumer_health_unreadable", error=str(exc), error_type=type(exc).__name__
                )
                self._heartbeat.missed(f"its state could not be read: {exc!r}")


async def consume_forever(
    consumer: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
    target: str = "",
    group: str = GROUP_ID,
    *,
    sleep: Sleep = asyncio.sleep,
    health: Health | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Start the consumer, apply everything that arrives, and stop it. Returns messages seen.

    Split from `run_consumer` so the `FRD-617` call sites — `consumer.start()`, where SASL, trust
    store and broker address are first proven, and the per-message receive line — can be driven
    with a fake broker. ``target`` is the broker address, passed in rather than read off a private
    attribute of the client.

    **An offset is committed here, once its event was applied or deliberately skipped**, and never
    by a timer (:func:`consumer_options`): a timer commits whatever the client has handed out, which
    moves past an event :func:`apply_one_message` is still holding, and a restart in that window
    loses it. A stop before the commit delivers the event again, which every handler takes twice
    without harm.

    **``stop`` ends it between two events** (`FRD-631` FR-4): the wait for the next one is given
    up, an event being applied is applied and committed first, and one held for another attempt is
    left uncommitted. Then the consumer leaves its group — so the broker hands the partitions on at
    once rather than after its session timeout — which a process killed after its grace period
    never did.

    ``health``, when given, is told of every step and looks at the consumer between events
    (:class:`Health`).
    """
    seen = 0
    looking = asyncio.create_task(health.watch(consumer)) if health is not None else None
    try:
        if not await _start(consumer, target, group, sleep, stop):
            return seen
        if health is not None:
            health.started()
        incoming = aiter(consumer)
        while True:
            message = await _next(incoming, stop)
            if message is None:
                return seen
            seen += 1
            with health.applying() if health is not None else contextlib.nullcontext():
                # `apply_one_message` swallows its own failures; `applied` carries the difference.
                with watch(
                    "kafka",
                    "consumer.receive",
                    topic=getattr(message, "topic", None),
                    partition=getattr(message, "partition", None),
                    offset=getattr(message, "offset", None),
                ) as call:
                    try:
                        applied = await apply_one_message(
                            sessionmaker, message, sleep=sleep, stop=stop
                        )
                    except StoppedWhileHolding:
                        _log.info(
                            "config_event_left_for_the_next_consumer",
                            event_type=decode_event_type(message.headers),
                            topic=getattr(message, "topic", None),
                            partition=getattr(message, "partition", None),
                            offset=getattr(message, "offset", None),
                        )
                        return seen
                    call.note(event_type=applied, applied=applied is not None)
                await _commit(consumer, message)
            if health is not None:
                health.applied()
    finally:
        if looking is not None:
            looking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await looking
        # Also after a start that failed: a client left open is reported by its finaliser while
        # the interpreter shuts down, as a traceback about something else entirely. And after a
        # stop: leaving the group is what lets the broker hand the partitions on at once.
        await consumer.stop()


async def _next(incoming: Any, stop: asyncio.Event | None) -> Any:
    """The next message, or ``None`` when there is none or ``stop`` was set while waiting."""
    if stop is not None and stop.is_set():
        return None
    receiving = asyncio.ensure_future(anext(incoming))
    if stop is None:
        try:
            return await receiving
        except StopAsyncIteration:
            return None
    stopping = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({receiving, stopping}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not stopping.done():
            stopping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stopping
    if not receiving.done():
        receiving.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await receiving
        return None
    try:
        return receiving.result()
    except StopAsyncIteration:
        return None


async def _start(
    consumer: Any, target: str, group: str, sleep: Sleep, stop: asyncio.Event | None = None
) -> bool:
    """``consumer.start()``, **waiting for a broker that is not ready yet** rather than exiting.

    At start the broker is often not up yet — a stack starting together, a broker restarting — or
    has not got the topics yet. Exiting would make that a traceback and a restart loop, which an
    orchestrator backs off from for longer each time, so configuration would arrive later than the
    broker. What aiokafka marks retriable is waited out, one line per attempt; anything else — a
    refused authorization, a broker it cannot speak to — still ends the process. A stop ends the
    waiting. Returns whether the consumer started.
    """
    failures = 0
    while True:
        if stop is not None and stop.is_set():
            return False
        try:
            with watch("kafka", "consumer.start", target=target, group=group):
                await consumer.start()
            return True
        except KafkaError as exc:
            if not exc.retriable:
                raise
            failures += 1
            delay = retry_delay(failures)
            _log.warning(
                "consumer_waiting_for_kafka",
                target=target,
                group=group,
                attempt=failures,
                retry_in_seconds=delay,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            if await pause(delay, sleep=sleep, stop=stop):
                return False


async def _commit(consumer: Any, message: Any) -> None:
    """Commit the position after ``message``, whose event was applied or deliberately skipped.

    A commit that fails — the group rebalancing, the broker away for a moment — is said, not
    raised: the next commit covers this one, and an event delivered again is applied again
    harmlessly.
    """
    position = {TopicPartition(message.topic, message.partition): message.offset + 1}
    try:
        await consumer.commit(position)
    except KafkaError as exc:
        _log.warning(
            "config_offset_not_committed",
            topic=message.topic,
            partition=message.partition,
            offset=message.offset,
            error=str(exc),
            error_type=type(exc).__name__,
        )


def consumer_options(settings: GatewaySettings) -> dict[str, Any]:
    """What the configuration consumer is built with, beside its topics."""
    return {
        "bootstrap_servers": settings.kafka_bootstrap_servers,
        "group_id": consumer_group(settings.kafka_stage),
        "auto_offset_reset": "earliest",
        # Committed by `consume_forever` once each event is applied or skipped, never on a timer
        # that moves past an event still being tried again.
        "enable_auto_commit": False,
        # No `value_deserializer`: the value is decoded inside `apply_one_message`'s guard, where a
        # message that is not JSON is skipped by name instead of stopping the consumer.
        # Authenticated like the relay that publishes: an open broker is a way to write straight
        # into the read-model this gateway's authorization is read from (`KafkaSecurity`).
        **settings.kafka_security().client_kwargs(),
    }


@contextlib.contextmanager
def stopped_by_signals(stop: asyncio.Event) -> Iterator[None]:
    """SIGTERM and SIGINT set ``stop`` for as long as the consumer runs.

    **Without them the consumer did not stop at all** as a container's first process: the kernel
    delivers no signal to PID 1 that it has left at its default, so `docker stop` waited its ten
    seconds, killed it — exit code 137 — and the consumer never left its group nor handed over
    the telemetry it held.
    """
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(number, stop.set)
        except NotImplementedError, RuntimeError, ValueError:
            # Not the main thread, or no signals on this platform: whoever runs it stops it.
            continue
        installed.append(number)
    try:
        yield
    finally:
        for number in installed:
            loop.remove_signal_handler(number)


async def run_consumer(
    settings: GatewaySettings, stop: asyncio.Event | None = None
) -> int:  # pragma: no cover
    from aiokafka import AIOKafkaConsumer

    # Before the first message, or the spans `apply_one_message` opens have no tracer (`FRD-615`).
    configure_worker(settings, process="consumer")

    engine = build_engine(settings.database_url(use_sqlite=False))
    # **No `create_all` here.** `gateway-migrate` runs the migrations first, and `create_all` beside
    # Alembic lets an older container resurrect a table a migration dropped (`FRD-114`).
    sessionmaker = build_sessionmaker(engine)

    options = consumer_options(settings)
    consumer = AIOKafkaConsumer(*subscriptions(settings.kafka_stage), **options)
    heartbeat = Heartbeat("consumer", MAX_AGE_SECONDS, log=_log)
    heartbeat.start()
    stop = stop or asyncio.Event()
    seen = 0
    try:
        with stopped_by_signals(stop):
            seen = await consume_forever(
                consumer,
                sessionmaker,
                settings.kafka_bootstrap_servers,
                options["group_id"],
                health=Health(heartbeat),
                stop=stop,
            )
    finally:
        await engine.dispose()
        _log.info("worker_stopped", worker="consumer", messages=seen)
    return seen


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    # Named before the settings are read: reading them from Vault writes this process's first lines.
    process_named("consumer")
    settings = GatewaySettings()
    try:
        asyncio.run(run_consumer(settings))
    finally:
        # The last lines, `worker_stopped` among them, while the process still decides the order.
        say_stopped(service=settings.app_name)
        flush_telemetry()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
