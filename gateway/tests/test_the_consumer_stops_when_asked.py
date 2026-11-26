"""The configuration consumer stops when it is told to (`FRD-631` FR-4).

On Stage T `docker compose stop` ended the relay and the retention sweep with exit code 0 and the
consumer with 137: it had no handler for SIGTERM, and as a container's first process a signal left
at its default is not delivered at all, so Docker waited its ten seconds and killed it. It never
left its consumer group — the broker held its partitions until the session timed out — and never
handed over the telemetry it held, its own `worker_stopped` included.

Now SIGTERM ends it **between two events**: the wait for the next one is given up, an event being
applied is applied and committed first, and one held for another attempt is left uncommitted for
the next consumer, which takes it twice without harm. Then it leaves its group.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import pytest
from aiokafka.structs import TopicPartition

from aira_common.kafka import EVENT_TYPE_HEADER, USECASE_TOPIC
from aira_gateway.consumer import worker
from aira_gateway.consumer.worker import consume_forever, stopped_by_signals
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


class Message:
    topic = USECASE_TOPIC
    partition = 0

    def __init__(self, offset: int) -> None:
        self.offset = offset
        self.headers = [(EVENT_TYPE_HEADER, b"usecase.upserted")]
        self.value = b'{"slug": "coding", "name": "Coding"}'


class Broker:
    """Hands out ``messages`` and then waits for more, as a quiet topic does — for ever."""

    def __init__(self, *messages: Message) -> None:
        self.messages = list(messages)
        self.committed: list[dict[TopicPartition, int]] = []
        self.stopped = False
        self.waiting = asyncio.Event()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        self.committed.append(dict(offsets))

    async def __aiter__(self):  # noqa: ANN204
        for message in self.messages:
            yield message
        self.waiting.set()
        await asyncio.Event().wait()


@pytest.fixture
async def sessions() -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_a_stop_while_waiting_for_the_next_event_ends_the_consumer_and_leaves_the_group(
    sessions: Any,
) -> None:
    broker, stop = Broker(Message(0)), asyncio.Event()
    running = asyncio.create_task(consume_forever(broker, sessions, stop=stop))
    await asyncio.wait_for(broker.waiting.wait(), timeout=5)

    stop.set()
    seen = await asyncio.wait_for(running, timeout=5)

    assert seen == 1
    assert broker.committed == [{TopicPartition(USECASE_TOPIC, 0): 1}]
    assert broker.stopped, "it left its group, so the broker hands its partitions on at once"


async def test_an_event_being_applied_is_applied_and_committed_before_it_stops(
    sessions: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, stop = Broker(Message(0), Message(1)), asyncio.Event()
    real = worker.apply_one_message

    async def applying(*args: Any, **kwargs: Any) -> str | None:
        # The signal arrives in the middle of the first event.
        stop.set()
        return await real(*args, **kwargs)

    monkeypatch.setattr(worker, "apply_one_message", applying)

    seen = await asyncio.wait_for(consume_forever(broker, sessions, stop=stop), timeout=5)

    assert seen == 1, "the second event is the next consumer's"
    assert broker.committed == [{TopicPartition(USECASE_TOPIC, 0): 1}]
    assert broker.stopped


async def test_an_event_held_for_another_attempt_is_left_uncommitted(
    sessions: Any, monkeypatch: pytest.MonkeyPatch, lines_logged_by: Any
) -> None:
    """The database is away; the event waits for it. A stop then ends the waiting — not by
    skipping the event, which would lose the change for good, but by leaving it to the next
    consumer, which reads it again from the last committed offset."""
    import psycopg.errors

    broker, stop = Broker(Message(0)), asyncio.Event()
    lines = lines_logged_by(worker)

    async def database_away(*_args: Any, **_kwargs: Any) -> None:
        raise psycopg.errors.AdminShutdown("terminating connection due to administrator command")

    monkeypatch.setattr(worker, "apply_event", database_away)

    async def sleep(_seconds: float) -> None:
        stop.set()
        await asyncio.sleep(0)

    seen = await asyncio.wait_for(consume_forever(broker, sessions, stop=stop, sleep=sleep), 5)

    assert seen == 1
    assert broker.committed == [], "never committed past an event that was not applied"
    assert broker.stopped
    assert [line["event"] for line in lines][-1] == "config_event_left_for_the_next_consumer"


async def test_a_stop_while_waiting_for_kafka_at_start_starts_nothing(sessions: Any) -> None:
    from aiokafka.errors import KafkaConnectionError

    class Away(Broker):
        async def start(self) -> None:
            raise KafkaConnectionError("Unable to bootstrap")

    broker, stop = Away(), asyncio.Event()

    async def sleep(_seconds: float) -> None:
        stop.set()

    seen = await asyncio.wait_for(consume_forever(broker, sessions, stop=stop, sleep=sleep), 5)

    assert seen == 0 and broker.stopped


async def test_sigterm_is_what_tells_it() -> None:
    """As a container's first process the signal is not delivered unless a handler asks for it."""
    stop = asyncio.Event()
    with stopped_by_signals(stop):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), timeout=5)

    assert stop.is_set()
    assert asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM) is False, (
        "the handler is taken away again once the consumer stopped"
    )
