"""Kafka topics + a thin producer abstraction (FRD-204).

The ``Producer`` protocol lets business logic (the management relay) be tested with an
in-memory fake; the aiokafka-backed producer is used by the real worker (and covered by
integration tests, hence ``# pragma: no cover`` on its I/O methods). Trace context (FRD-001)
is propagated on Kafka headers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from aira_common.observability import kafka_headers_from_context

USECASE_TOPIC = "aira.usecases"
MEMBERSHIP_TOPIC = "aira.memberships"
API_KEY_TOPIC = "aira.api-keys"
PIPELINE_TOPIC = "aira.pipelines"
BUDGET_TOPIC = "aira.budgets"
RATE_LIMIT_TOPIC = "aira.rate-limits"
MODEL_TOPIC = "aira.models"

EVENT_TYPE_HEADER = "event_type"


@dataclass(frozen=True, slots=True)
class KafkaRecord:
    """A record to publish: ``topic``/``key`` for partitioning, ``event_type`` + ``payload``."""

    topic: str
    key: str
    event_type: str
    payload: dict[str, Any]


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
    """Real aiokafka-backed producer (integration-tested)."""

    def __init__(self, bootstrap_servers: str) -> None:  # pragma: no cover
        self._bootstrap_servers = bootstrap_servers
        self._producer: Any = None

    async def start(self) -> None:  # pragma: no cover
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
        )
        await self._producer.start()

    async def stop(self) -> None:  # pragma: no cover
        if self._producer is not None:
            await self._producer.stop()

    async def send(self, record: KafkaRecord) -> None:  # pragma: no cover
        headers = [(EVENT_TYPE_HEADER, record.event_type.encode("utf-8"))]
        headers.extend(kafka_headers_from_context())
        await self._producer.send_and_wait(
            record.topic, value=record.payload, key=record.key.encode("utf-8"), headers=headers
        )
