"""Kafka topics, and a thin producer abstraction (`FRD-204`).

The ``Producer`` protocol lets business logic (the management relay) be tested with an in-memory
fake. Trace context travels on message headers (`FRD-001`, `FRD-615`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aira_common.integration_debug import watch
from aira_common.observability import kafka_headers_for

USECASE_TOPIC = "aira.usecases"
MEMBERSHIP_TOPIC = "aira.memberships"
API_KEY_TOPIC = "aira.api-keys"
PIPELINE_TOPIC = "aira.pipelines"
BUDGET_TOPIC = "aira.budgets"
RATE_LIMIT_TOPIC = "aira.rate-limits"
MODEL_TOPIC = "aira.models"
ANOMALY_RULE_TOPIC = "aira.anomaly-rules"

#: The header naming a record's event type.
EVENT_TYPE_HEADER = "event_type"


@dataclass(frozen=True, slots=True)
class KafkaSecurity:
    """How both planes authenticate to the broker, in one place.

    **The bus is a trust boundary.** The gateway applies whatever arrives on these topics to the
    read-model its authorization is derived from, so an unauthenticated broker lets anybody who can
    reach it publish their own API key or group grant — `FRD-204`'s idempotent consumer assumes an
    authenticated bus. The `PLAINTEXT` default keeps a laptop and the Compose stack working; both
    planes refuse to start on it outside `local` (`ADR-0015`).
    """

    protocol: str = "PLAINTEXT"
    sasl_mechanism: str = ""
    sasl_username: str = ""
    sasl_password: str = ""
    #: Trust store for the broker's certificate. Empty uses the system trust store; a private CA is
    #: named here.
    ssl_cafile: str = ""

    @property
    def is_plaintext(self) -> bool:
        return self.protocol.strip().upper() in {"", "PLAINTEXT"}

    def client_kwargs(self) -> dict[str, Any]:
        """The keyword arguments both aiokafka clients take.

        Built once, so a producer that authenticates beside a consumer that does not cannot happen.
        """
        protocol = self.protocol.strip().upper() or "PLAINTEXT"
        kwargs: dict[str, Any] = {"security_protocol": protocol}
        if protocol in {"SASL_PLAINTEXT", "SASL_SSL"}:
            kwargs["sasl_mechanism"] = self.sasl_mechanism.strip().upper() or "SCRAM-SHA-512"
            kwargs["sasl_plain_username"] = self.sasl_username
            kwargs["sasl_plain_password"] = self.sasl_password
        if protocol in {"SSL", "SASL_SSL"}:
            import ssl

            kwargs["ssl_context"] = ssl.create_default_context(cafile=self.ssl_cafile or None)
        return kwargs


@dataclass(frozen=True, slots=True)
class KafkaRecord:
    """A record to publish: ``topic``/``key`` for partitioning, ``event_type`` + ``payload``."""

    topic: str
    key: str
    event_type: str
    payload: dict[str, Any]
    #: The W3C trace context of the request that **caused** this event, if one was captured. An
    #: outbox publishes later, from another process with no span, so the causing context is stored
    #: on the row and restored here (`FRD-615`). Empty when there was no caller.
    traceparent: str = ""


class Producer(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, record: KafkaRecord) -> None: ...


class InMemoryProducer:
    """Test/dev producer that records what would be sent."""

    def __init__(self) -> None:
        self.sent: list[KafkaRecord] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, record: KafkaRecord) -> None:
        self.sent.append(record)


class AiokafkaProducer:
    """Real aiokafka-backed producer.

    The client comes from an injected factory, so `start()` — where protocol, SASL mechanism,
    credentials, trust store and broker address are first tested together — can be driven with a
    fake broker, and its `FRD-617` lines are tested rather than assumed.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        security: KafkaSecurity | None = None,
        *,
        factory: Callable[..., Any] | None = None,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._security = security or KafkaSecurity()
        self._factory = factory or _aiokafka_producer
        self._producer: Any = None

    async def start(self) -> None:
        with watch(
            "kafka",
            "producer.start",
            target=self._bootstrap_servers,
            protocol=self._security.protocol,
        ):
            self._producer = self._factory(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda value: json.dumps(value).encode("utf-8"),
                **self._security.client_kwargs(),
            )
            await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            with watch("kafka", "producer.stop", target=self._bootstrap_servers):
                await self._producer.stop()

    async def send(self, record: KafkaRecord) -> None:
        headers = [(EVENT_TYPE_HEADER, record.event_type.encode("utf-8"))]
        headers.extend(kafka_headers_for(record.traceparent))
        with watch(
            "kafka",
            "producer.send",
            target=self._bootstrap_servers,
            topic=record.topic,
            key=record.key,
            event_type=record.event_type,
            traced=bool(record.traceparent),
        ) as call:
            metadata = await self._producer.send_and_wait(
                record.topic, value=record.payload, key=record.key.encode("utf-8"), headers=headers
            )
            # Where it landed: what somebody takes to `kafka-console-consumer` when the far end
            # says it never saw the event.
            call.note(
                partition=getattr(metadata, "partition", None),
                offset=getattr(metadata, "offset", None),
            )


def _aiokafka_producer(**kwargs: Any) -> Any:  # pragma: no cover - the real client, by name
    """The default factory. Imported here so aiokafka stays absent from a process that never
    produces — the management API imports this module for its topic names alone."""
    from aiokafka import AIOKafkaProducer

    return AIOKafkaProducer(**kwargs)
