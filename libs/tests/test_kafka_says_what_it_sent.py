"""What the producer did with a record, and what the broker said (`FRD-617` §3.3).

`AiokafkaProducer.start()` is the one call in this system where a Kafka credential — a
`security_protocol`, a SASL mechanism, a username, a trust store — is proven against a real
broker, and it was three lines with no logging behind a `# pragma: no cover`. `send_and_wait`
returns the partition and offset a record landed on and the return value was thrown away.

Driven through the class with an injected client factory rather than by asserting that `watch` was
called: the factory is what makes this a test of the wire and not of the two ends.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from aira_common.integration_debug import configure_integration_debug
from aira_common.kafka import AiokafkaProducer, KafkaRecord, KafkaSecurity
from aira_common.logging import configure_logging


@pytest.fixture(autouse=True)
def _channel() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    configure_integration_debug("kafka")
    yield
    configure_integration_debug("")


def calls(capsys: Any) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{") and '"integration_call"' in line
    ]


class Metadata:
    partition = 2
    offset = 4711


class FakeBroker:
    """Stands in for `AIOKafkaProducer`."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.sent: list[tuple[str, Any]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send_and_wait(self, topic: str, **kwargs: Any) -> Metadata:
        self.sent.append((topic, kwargs))
        return Metadata()


def _producer(factory: Any = None, security: KafkaSecurity | None = None) -> AiokafkaProducer:
    return AiokafkaProducer(
        "broker-a:9093,broker-b:9093", security or KafkaSecurity(), factory=factory or FakeBroker
    )


async def test_connecting_to_the_broker_says_where_and_how(capsys: Any) -> None:
    await _producer(security=KafkaSecurity(protocol="SASL_SSL", sasl_username="aira")).start()

    (line,) = [c for c in calls(capsys) if c["operation"] == "producer.start"]
    assert line["outcome"] == "ok"
    assert line["target"] == "broker-a:9093,broker-b:9093"
    assert line["protocol"] == "SASL_SSL"


async def test_a_broker_that_refuses_the_credential_says_so(capsys: Any) -> None:
    class Refusing(FakeBroker):
        async def start(self) -> None:
            raise ConnectionError("Unable to authenticate: SCRAM-SHA-512 mechanism not enabled")

    with pytest.raises(ConnectionError):
        await _producer(Refusing).start()

    (line,) = [c for c in calls(capsys) if c["operation"] == "producer.start"]
    assert line["outcome"] == "failed"
    assert "SCRAM-SHA-512" in line["error"]


async def test_a_sent_record_reports_where_it_landed(capsys: Any) -> None:
    """A partition and an offset are what somebody takes to `kafka-console-consumer` when the far
    end says it never saw the event. Without them the producer's word is the only evidence."""
    producer = _producer()
    await producer.start()
    await producer.send(
        KafkaRecord(
            topic="aira.usecases",
            key="kundenservice",
            event_type="use_case.upserted",
            payload={"slug": "kundenservice"},
            traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        )
    )

    (line,) = [c for c in calls(capsys) if c["operation"] == "producer.send"]
    assert line["topic"] == "aira.usecases"
    assert line["event_type"] == "use_case.upserted"
    assert (line["partition"], line["offset"]) == (2, 4711)
    # Whether the message carries the causing request's trace context (`FRD-615`) — the field that
    # would have made an outbox publishing empty `traceparent`s visible in an afternoon.
    assert line["traced"] is True


async def test_a_record_published_outside_a_request_says_it_carries_no_trace(capsys: Any) -> None:
    producer = _producer()
    await producer.start()
    await producer.send(KafkaRecord("aira.models", "m", "model.upserted", {}))

    (line,) = [c for c in calls(capsys) if c["operation"] == "producer.send"]
    assert line["traced"] is False


async def test_the_record_still_reaches_the_broker_unchanged(capsys: Any) -> None:
    """`FR-6`: the channel observes, it does not participate."""
    broker = FakeBroker()
    producer = _producer(lambda **kwargs: broker)
    await producer.start()
    await producer.send(KafkaRecord("aira.budgets", "uc-a", "budget.upserted", {"amount": 5}))
    await producer.stop()

    (topic, sent) = broker.sent[0]
    assert topic == "aira.budgets"
    assert sent["value"] == {"amount": 5}
    assert sent["key"] == b"uc-a"
    assert ("event_type", b"budget.upserted") in sent["headers"]
    assert broker.started is False


async def test_stopping_a_producer_that_never_started_says_nothing(capsys: Any) -> None:
    await _producer().stop()
    assert [c for c in calls(capsys) if c["operation"] == "producer.stop"] == []


async def test_nothing_is_said_when_kafka_is_not_watched(capsys: Any) -> None:
    configure_integration_debug("otel")
    producer = _producer()
    await producer.start()
    await producer.send(KafkaRecord("aira.models", "m", "model.upserted", {}))
    assert calls(capsys) == []
