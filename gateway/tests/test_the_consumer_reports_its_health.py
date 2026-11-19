"""The configuration consumer's heartbeat is progress, never only life (`FRD-153`).

The consumer cannot stop on its own: it waits for a broker that is away and holds an event the
database cannot take, for as long as it takes. Both are right — and both left the process up and
the container "Up" while no configuration was applied, a revoked key included. Its heartbeat is
what its container's healthcheck reads now, so these are the cases where it must **not** beat:
waiting for the broker, holding an event, no partition assigned, a broker that stopped answering.
And the ones where it must: started, an event applied or skipped, idle with a broker that answers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError
from aiokafka.structs import TopicPartition
from sqlalchemy import exc as sa_exc
from sqlalchemy import select

from aira_common.heartbeat import Heartbeat, judge, parse
from aira_common.kafka import EVENT_TYPE_HEADER, USECASE_TOPIC
from aira_gateway.consumer import worker
from aira_gateway.consumer.worker import (
    BROKER_SILENCE_SECONDS,
    IDLE_BEAT_SECONDS,
    MAX_AGE_SECONDS,
    Health,
    consume_forever,
)
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import UseCaseRead

PARTITION = TopicPartition(USECASE_TOPIC, 0)


class Lines:
    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Message:
    topic = USECASE_TOPIC
    partition = 0
    offset = 0
    headers = [(EVENT_TYPE_HEADER, b"usecase.upserted")]
    value = b'{"slug": "coding", "name": "Coding"}'


class Broker:
    """A broker that accepts the consumer after ``unreachable`` refused starts, assigns it one
    partition, answers its fetches until told not to, and delivers ``messages``."""

    def __init__(self, clock: Clock, *, messages: int = 1, unreachable: int = 0) -> None:
        self.clock = clock
        self.messages = messages
        self.unreachable = unreachable
        self.starts = 0
        self.assigned = {PARTITION}
        self.answering = True
        self.silent_since: float | None = None
        self.on_start: list[Any] = []

    async def start(self) -> None:
        self.starts += 1
        for look in self.on_start:
            look()
        if self.starts <= self.unreachable:
            raise KafkaConnectionError("Unable to bootstrap from [('kafka', 9092)]")

    async def stop(self) -> None:
        return None

    async def commit(self, offsets: dict[Any, int]) -> None:
        return None

    def assignment(self) -> set[TopicPartition]:
        return set(self.assigned)

    def last_poll_timestamp(self, partition: TopicPartition) -> float | None:
        assert partition in self.assigned, "aiokafka refuses a partition it has not got"
        if self.silent_since is not None:
            return self.silent_since * 1000.0
        return self.clock.now * 1000.0 if self.answering else None

    async def __aiter__(self):  # noqa: ANN204
        for _ in range(self.messages):
            yield Message()


async def _never(_seconds: float) -> None:
    """A watch that never looks on its own: these tests look when they choose to."""
    await asyncio.Event().wait()


@pytest.fixture
async def sessions() -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> Lines:
    recorded = Lines()
    monkeypatch.setattr(worker, "_log", recorded)
    return recorded


def _health(tmp_path: Path, clock: Clock, lines: Lines) -> tuple[Health, Heartbeat]:
    beat = Heartbeat("consumer", MAX_AGE_SECONDS, log=lines, path=tmp_path / "beat", clock=clock)
    return Health(beat, clock=clock, tick=_never), beat


def _verdict(tmp_path: Path, clock: Clock) -> bool | None:
    """What the container's healthcheck would say now; ``None`` while there is no heartbeat."""
    beat = tmp_path / "beat"
    return judge(parse(beat.read_bytes()), clock.now)[0] if beat.exists() else None


class Sleeps:
    """The consumer's pauses, on the test's clock, with a look at the consumer during each."""

    def __init__(self, clock: Clock, look: Any = None) -> None:
        self.clock = clock
        self.look = look
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock.now += delay
        if self.look is not None:
            self.look()


# == when it beats =================================================================================


async def test_it_beats_once_its_broker_accepts_it_and_after_each_event(
    tmp_path: Path, sessions: Any, lines: Lines
) -> None:
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock, unreachable=2)
    seen_at_start: list[bool | None] = []
    broker.on_start.append(lambda: seen_at_start.append(_verdict(tmp_path, clock)))
    sleeps = Sleeps(clock)

    await consume_forever(broker, sessions, "kafka:9092", sleep=sleeps, health=health)

    assert seen_at_start == [None, None, None], "nothing before the broker accepted it"
    assert _verdict(tmp_path, clock) is True
    async with sessions() as session:
        assert (await session.execute(select(UseCaseRead.slug))).scalars().all() == ["coding"]
    assert [fields["quiet_seconds"] for fields in lines.of("worker_healthy")] == [3.0]


def test_an_idle_consumer_its_broker_answers_beats(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock)
    health.started()
    clock.now += 60

    health.check(broker)

    assert _verdict(tmp_path, clock) is True
    assert parse((tmp_path / "beat").read_bytes()).written_at == clock.now, (
        "the look wrote it, not the start a minute ago"
    )


async def test_the_watch_looks_every_idle_interval(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    beat = Heartbeat("consumer", MAX_AGE_SECONDS, log=lines, path=tmp_path / "beat", clock=clock)
    ticks: list[float] = []

    async def tick(seconds: float) -> None:
        ticks.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    health = Health(beat, clock=clock, tick=tick)
    health.started()
    looking = asyncio.create_task(health.watch(Broker(clock)))
    for _ in range(5):
        await asyncio.sleep(0)
    looking.cancel()

    assert ticks[:3] == [IDLE_BEAT_SECONDS] * 3
    assert parse((tmp_path / "beat").read_bytes()).written_at > 1_000_000.0 + IDLE_BEAT_SECONDS


# == when it does not ==============================================================================


def test_a_look_while_it_waits_for_its_broker_is_no_beat(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    clock.now += MAX_AGE_SECONDS + 1

    health.check(Broker(clock))

    assert _verdict(tmp_path, clock) is False
    assert lines.of("worker_unhealthy")[0]["reason"] == "waiting for Kafka"
    said = judge(parse((tmp_path / "beat").read_bytes()), clock.now)[1]
    assert said == "consumer: no progress since it started 91 s ago — waiting for Kafka"


async def test_an_event_held_for_retry_is_no_beat_however_long_it_is_held(
    tmp_path: Path, sessions: Any, lines: Lines
) -> None:
    """**The case the heartbeat exists for.** A database failure that passes holds the event and
    every later one, for as long as it takes; the process is up, its broker answers, and nothing is
    applied. A look during the hold must not beat — or a database that never comes back would be a
    healthy consumer applying nothing for good."""
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock)
    verdicts: list[bool | None] = []

    def look() -> None:
        health.check(broker)
        verdicts.append(_verdict(tmp_path, clock))

    failures = 8  # 1 + 2 + 4 + 8 + 16 + 30 + 30 + 30 = 121 s held

    class Flaky:
        def __init__(self) -> None:
            self.left = failures

        def __call__(self) -> Any:
            if self.left:
                self.left -= 1
                raise sa_exc.DisconnectionError("server closed the connection unexpectedly")
            return sessions()

    sleeps = Sleeps(clock, look)

    await consume_forever(broker, Flaky(), "kafka:9092", sleep=sleeps, health=health)

    assert sum(sleeps.delays) == 121.0
    assert verdicts[:5] == [True] * 5, "a hold shorter than the age reads as it is: healthy"
    assert verdicts[-1] is False, "held past the age, the container reads unhealthy"
    [unhealthy] = lines.of("worker_unhealthy")
    assert unhealthy["reason"] == "an event is being applied or held for retry"
    assert _verdict(tmp_path, clock) is True, "the event applied, and that was progress again"
    assert lines.of("worker_healthy")[-1]["quiet_seconds"] == 121.0


def test_no_partition_assigned_is_no_beat(tmp_path: Path, lines: Lines) -> None:
    """A rebalance that never ends assigns nothing, and nothing is consumed meanwhile."""
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock)
    broker.assigned = set()
    health.started()
    clock.now += MAX_AGE_SECONDS + 1

    health.check(broker)

    assert _verdict(tmp_path, clock) is False
    assert lines.of("worker_unhealthy")[0]["reason"] == "no partition is assigned to it"


def test_a_broker_that_stopped_answering_is_no_beat(tmp_path: Path, lines: Lines) -> None:
    """A broker that went away leaves the consumer its assignment, idle, waiting in `getone`: only
    the fetches it no longer answers tell the difference from a quiet topic."""
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock)
    health.started()
    broker.silent_since = clock.now

    clock.now += BROKER_SILENCE_SECONDS - 1
    health.check(broker)
    assert parse((tmp_path / "beat").read_bytes()).written_at == clock.now, "still in contact"

    clock.now += 2
    health.check(broker)
    clock.now += MAX_AGE_SECONDS
    health.check(broker)

    assert _verdict(tmp_path, clock) is False
    assert lines.of("worker_unhealthy")[0]["reason"] == (
        f"its broker has not answered a fetch for {USECASE_TOPIC} partition 0 in "
        f"{BROKER_SILENCE_SECONDS:.0f} s"
    )


def test_a_partition_not_fetched_yet_is_no_beat(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    broker = Broker(clock)
    broker.answering = False
    health.started()
    clock.now += MAX_AGE_SECONDS + 1

    health.check(broker)

    assert _verdict(tmp_path, clock) is False


def test_every_assigned_partition_must_be_answered(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    health, _beat = _health(tmp_path, clock, lines)
    second = TopicPartition("aira.api-keys", 0)
    broker = Broker(clock)
    broker.assigned = {PARTITION, second}
    answered = {PARTITION: clock.now * 1000.0, second: (clock.now - 300) * 1000.0}
    broker.last_poll_timestamp = answered.__getitem__  # type: ignore[method-assign]
    health.started()
    clock.now += MAX_AGE_SECONDS + 1
    answered[PARTITION] = clock.now * 1000.0

    health.check(broker)

    assert "aira.api-keys partition 0" in lines.of("worker_unhealthy")[0]["reason"]


async def test_a_look_that_fails_is_no_beat_and_is_said(tmp_path: Path, lines: Lines) -> None:
    clock = Clock()
    beat = Heartbeat("consumer", MAX_AGE_SECONDS, log=lines, path=tmp_path / "beat", clock=clock)

    class Broken(Broker):
        def assignment(self) -> set[TopicPartition]:
            raise RuntimeError("the client is closed")

    async def tick(seconds: float) -> None:
        clock.now += seconds
        await asyncio.sleep(0)

    health = Health(beat, clock=clock, tick=tick)
    health.started()
    clock.now += MAX_AGE_SECONDS
    looking = asyncio.create_task(health.watch(Broken(clock)))
    for _ in range(3):
        await asyncio.sleep(0)
    looking.cancel()

    assert lines.of("consumer_health_unreadable")[0]["error"] == "the client is closed"
    assert "its state could not be read" in lines.of("worker_unhealthy")[0]["reason"]


async def test_the_watch_ends_with_the_consumer(
    tmp_path: Path, sessions: Any, lines: Lines
) -> None:
    clock = Clock()
    beat = Heartbeat("consumer", MAX_AGE_SECONDS, log=lines, path=tmp_path / "beat", clock=clock)
    health = Health(beat, clock=clock)

    await consume_forever(Broker(clock), sessions, sleep=Sleeps(clock), health=health)

    watching = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "") == "Health.watch"
    ]
    assert watching == []


# == what it relies on in the real client ==========================================================


async def test_the_real_client_offers_what_the_watch_reads() -> None:
    """The watch asks aiokafka's consumer for its assignment and for the moment its broker last
    answered each partition. A version that renamed either would leave every consumer unhealthy —
    loudly, but only once deployed."""
    consumer = AIOKafkaConsumer(USECASE_TOPIC, bootstrap_servers="127.0.0.1:9", group_id="probe")

    assert consumer.assignment() == set()
    assert callable(consumer.last_poll_timestamp)
