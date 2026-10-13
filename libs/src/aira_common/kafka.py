"""Kafka topics, and a thin producer abstraction (`FRD-204`).

The ``Producer`` protocol lets business logic (the management relay) be tested with an in-memory
fake. Trace context travels on message headers (`FRD-001`, `FRD-615`).
"""

from __future__ import annotations

import json
import re
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
#: What each role may do and which group confers it (`FRD-614` FR-8).
ROLE_TOPIC = "aira.roles"

#: Every configuration topic, in the one list both planes read: Management publishes to them and
#: the gateway subscribes to every one.
CONFIG_TOPICS: tuple[str, ...] = (
    USECASE_TOPIC,
    MEMBERSHIP_TOPIC,
    API_KEY_TOPIC,
    PIPELINE_TOPIC,
    BUDGET_TOPIC,
    RATE_LIMIT_TOPIC,
    MODEL_TOPIC,
    ANOMALY_RULE_TOPIC,
    ROLE_TOPIC,
)

#: The gateway's consumer group before any stage.
CONSUMER_GROUP = "aira-gateway"

#: One short segment of lower-case letters and digits. It becomes part of every topic name, so a dot
#: would add a segment and a capital would make a second name for the same stage.
_STAGE = re.compile(r"[a-z0-9]{1,16}")


class KafkaStageError(ValueError):
    """A stage that cannot be part of a topic name. Raised at start-up, never at request time."""


def validate_stage(stage: str) -> str:
    """``stage`` as it goes into a name, or empty for none; refused rather than repaired."""
    cleaned = stage.strip()
    if cleaned and not _STAGE.fullmatch(cleaned):
        raise KafkaStageError(
            f"'{stage}' is not a Kafka stage: one to sixteen lower-case letters or digits, "
            "such as 't'."
        )
    return cleaned


def staged(topic: str, stage: str) -> str:
    """The topic for ``stage``: ``aira.usecases`` without one, ``aira.t.usecases`` for ``t``.

    The stage follows `aira.`, so one prefixed ACL — `aira.t.` — covers one stage's topics and
    nothing else on a cluster several stages share. The gateway builds its authorization from
    these topics, so one stage must not be able to write another's.
    """
    cleaned = validate_stage(stage)
    if not cleaned:
        return topic
    head, _, rest = topic.partition(".")
    return f"{head}.{cleaned}.{rest}"


def consumer_group(stage: str) -> str:
    """The gateway's consumer group for ``stage``, named like its topics."""
    cleaned = validate_stage(stage)
    return f"{CONSUMER_GROUP}.{cleaned}" if cleaned else CONSUMER_GROUP


#: The header naming a record's event type.
EVENT_TYPE_HEADER = "event_type"

#: Kafka's own port, for a broker written without one.
DEFAULT_KAFKA_PORT = 9092


def parse_bootstrap_servers(value: str) -> tuple[tuple[str, int], ...]:
    """``(host, port)`` per entry of a comma-separated broker list, as a Kafka client dials it.

    A bare host takes Kafka's port, and an IPv6 broker is written ``[address]:port`` — the brackets
    are not part of the host. An entry nothing could dial raises `ValueError` naming it, so a
    settings validator can refuse it at startup rather than a readiness probe at every request.
    """
    return tuple(_broker(entry.strip()) for entry in value.split(",") if entry.strip())


def _broker(entry: str) -> tuple[str, int]:
    port_text: str | None
    if entry.startswith("["):
        host, closed, rest = entry[1:].partition("]")
        if not closed or (rest and not rest.startswith(":")):
            raise ValueError(f"{entry!r} is neither [address] nor [address]:port")
        port_text = rest[1:] if rest else None
    elif entry.count(":") > 1:
        raise ValueError(f"{entry!r}: write an IPv6 broker as [address]:port")
    else:
        host, colon, after = entry.partition(":")
        port_text = after if colon else None
    if not host:
        raise ValueError(f"{entry!r} names no host")
    if port_text is None:
        return host, DEFAULT_KAFKA_PORT
    if not (port_text.isascii() and port_text.isdigit() and 0 < int(port_text) < 65536):
        raise ValueError(f"{entry!r}: the port must be a number from 1 to 65535")
    return host, int(port_text)


#: The largest request the producer sends — aiokafka's own `max_request_size` default, passed to it
#: explicitly, so this is the producer's limit rather than a copy of it.
MAX_REQUEST_BYTES = 1_048_576

#: The largest event value either plane writes or sends. Below the request limit by room for the
#: key, the headers and the record's framing, so a value within it is never refused for its size;
#: Management refuses a larger one while the change can still be rolled back.
MAX_EVENT_BYTES = 1_000_000


def encode_event(payload: dict[str, Any]) -> bytes:
    """An event's value as it goes on the wire — the producer's serializer, and what every bound
    on an event's size measures, so the two cannot disagree about a byte."""
    return json.dumps(payload).encode("utf-8")


class RecordRefused(Exception):
    """The broker will never accept this record, however often it is sent — it is too large.

    Distinct from every other send failure, which may pass: a publisher retrying this one at the
    head of an ordered queue would hold back everything behind it for good.
    """


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
                value_serializer=encode_event,
                max_request_size=MAX_REQUEST_BYTES,
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
            try:
                metadata = await self._producer.send_and_wait(
                    record.topic,
                    value=record.payload,
                    key=record.key.encode("utf-8"),
                    headers=headers,
                )
            except Exception as exc:
                if _refused_for_good(exc):
                    raise RecordRefused(str(exc)) from exc
                raise
            # Where it landed: what somebody takes to `kafka-console-consumer` when the far end
            # says it never saw the event.
            call.note(
                partition=getattr(metadata, "partition", None),
                offset=getattr(metadata, "offset", None),
            )


def _refused_for_good(exc: Exception) -> bool:
    """Whether aiokafka refused a record for its size — the client's own check or the broker's.
    Imported here for the reason `_aiokafka_producer` is."""
    from aiokafka.errors import MessageSizeTooLargeError

    return isinstance(exc, MessageSizeTooLargeError)


def _aiokafka_producer(**kwargs: Any) -> Any:  # pragma: no cover - the real client, by name
    """The default factory. Imported here so aiokafka stays absent from a process that never
    produces — the management API imports this module for its topic names alone."""
    from aiokafka import AIOKafkaProducer

    return AIOKafkaProducer(**kwargs)
