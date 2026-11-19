"""The configuration consumer waits for a broker that is not ready yet, rather than exiting.

At start the broker is often not up yet — a stack starting together, a broker restarting beside the
consumer — or has not got its topics yet. Exiting turns that into a traceback and a restart loop,
and under an orchestrator that backs off from a crashing container for longer each time,
configuration then arrives later than the broker does. A client left open by a failed start adds a
second traceback at shutdown, from its finaliser, about something else entirely.

So a failure aiokafka itself marks retriable is waited out, with a pause that grows to a ceiling
and one line per attempt; anything else still ends the process — after closing the client.
"""

from __future__ import annotations

import gc
import socket
import warnings
from typing import Any

import pytest
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import (
    KafkaConnectionError,
    UnknownTopicOrPartitionError,
    UnrecognizedBrokerVersion,
)
from sqlalchemy import select

from aira_common.kafka import EVENT_TYPE_HEADER, USECASE_TOPIC
from aira_gateway.consumer import worker
from aira_gateway.consumer.worker import RETRY_LONGEST_SECONDS, consume_forever
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import UseCaseRead


class _Lines:
    """Stands in for the worker's logger: what it said, by event."""

    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> _Lines:
    recorded = _Lines()
    monkeypatch.setattr(worker, "_log", recorded)
    return recorded


@pytest.fixture
async def sessions() -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


class _Message:
    topic = USECASE_TOPIC
    partition = 0
    offset = 0
    headers = [(EVENT_TYPE_HEADER, b"usecase.upserted")]
    value = b'{"slug": "coding", "name": "Coding"}'


class _LateBroker:
    """A broker that cannot be reached for the first ``unreachable`` starts, then delivers."""

    def __init__(self, unreachable: int, error: Exception | None = None) -> None:
        self.unreachable = unreachable
        self.error = error or KafkaConnectionError("Unable to bootstrap from [('kafka', 9092)]")
        self.starts = 0
        self.stopped = False

    async def start(self) -> None:
        self.starts += 1
        if self.starts <= self.unreachable:
            raise self.error

    async def stop(self) -> None:
        self.stopped = True

    async def commit(self, offsets: dict[Any, int]) -> None:
        return None

    async def __aiter__(self):  # noqa: ANN204
        yield _Message()


class _Sleeps:
    def __init__(self, stop_after: int | None = None) -> None:
        self.delays: list[float] = []
        self.stop_after = stop_after

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self.stop_after is not None and len(self.delays) >= self.stop_after:
            raise _Stopped


class _Stopped(Exception):
    """The test's way of ending a wait that would otherwise last as long as the broker is away."""


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(KafkaConnectionError("Unable to bootstrap from [('kafka', 9092)]"), id="away"),
        # A broker that is up, before its topics are: what aiokafka raises after its own wait.
        pytest.param(UnknownTopicOrPartitionError(), id="no-topics-yet"),
    ],
)
async def test_a_broker_that_comes_late_is_waited_for(
    sessions: Any, lines: _Lines, error: Exception
) -> None:
    broker = _LateBroker(unreachable=2, error=error)
    sleeps = _Sleeps()

    seen = await consume_forever(broker, sessions, "kafka:9092", sleep=sleeps)

    assert (broker.starts, seen) == (3, 1)
    async with sessions() as session:
        assert (await session.execute(select(UseCaseRead.slug))).scalars().all() == ["coding"]
    assert sleeps.delays == [1.0, 2.0]
    said = lines.of("consumer_waiting_for_kafka")
    assert [line["attempt"] for line in said] == [1, 2], "one line per attempt"
    assert all(line["target"] == "kafka:9092" for line in said)
    assert broker.stopped


async def test_the_wait_grows_to_a_ceiling(sessions: Any, lines: _Lines) -> None:
    sleeps = _Sleeps()

    await consume_forever(_LateBroker(unreachable=9), sessions, sleep=sleeps)

    assert sleeps.delays[:3] == [1.0, 2.0, 4.0]
    assert sleeps.delays[-3:] == [RETRY_LONGEST_SECONDS] * 3


async def test_a_failure_that_is_not_the_broker_being_away_still_ends_the_process(
    sessions: Any, lines: _Lines
) -> None:
    """Waiting is for what passes. A client that cannot speak to this broker at all is a
    configuration to fix, and it still stops the process — with its client closed."""
    broker = _LateBroker(unreachable=1, error=UnrecognizedBrokerVersion())
    sleeps = _Sleeps()

    with pytest.raises(UnrecognizedBrokerVersion):
        await consume_forever(broker, sessions, sleep=sleeps)

    assert sleeps.delays == []
    assert broker.stopped


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_the_real_client_is_started_again_and_closed_after(
    sessions: Any, lines: _Lines
) -> None:
    """With aiokafka's own consumer, because the wait starts the **same** client again: a stand-in
    would allow that whatever the client does. A failed bootstrap leaves it startable, and the
    client is closed when the wait ends — so nothing is left for its finaliser to complain about."""
    target = f"127.0.0.1:{_closed_port()}"
    consumer = AIOKafkaConsumer(
        USECASE_TOPIC, bootstrap_servers=target, group_id="aira-gateway-test"
    )

    with pytest.raises(_Stopped):
        await consume_forever(consumer, sessions, target, sleep=_Sleeps(stop_after=2))

    assert [line["error_type"] for line in lines.of("consumer_waiting_for_kafka")] == [
        "KafkaConnectionError",
        "KafkaConnectionError",
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del consumer
        gc.collect()
    assert not [w for w in caught if "Unclosed AIOKafkaConsumer" in str(w.message)]
