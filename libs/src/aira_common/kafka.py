"""Kafka topics + a thin producer abstraction (FRD-204).

The ``Producer`` protocol lets business logic (the management relay) be tested with an
in-memory fake; the aiokafka-backed producer is used by the real worker (and covered by
integration tests, hence ``# pragma: no cover`` on its I/O methods). Trace context (FRD-001)
is propagated on Kafka headers.
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

EVENT_TYPE_HEADER = "event_type"


@dataclass(frozen=True, slots=True)
class KafkaSecurity:
    """How both planes authenticate to the broker, in one place (2026-08-09).

    **The bus is a trust boundary, and it had none.** Producer and consumer connected with
    `bootstrap_servers` and nothing else — no protocol, no mechanism, no way to configure one. The
    gateway applies whatever arrives on these topics straight into the read-model its
    authorization is derived from, so anybody who could reach the broker could publish
    `api_key.created` with a hash of their choosing, or `use_case_group.granted` naming a group
    they are in, and hold administrator access to any use case. No credential is needed and no
    audit row is written, because from the gateway's side nothing unusual happened: configuration
    arrived, exactly as configuration does.

    Applying events without question is the right design *if* the bus is authenticated — that is
    what `FRD-204`'s idempotent consumer assumes. There was simply no way to make it true.

    Defaults reproduce the previous behaviour (`PLAINTEXT`) so a laptop and the Compose stack keep
    working; both planes refuse to start on it outside `local` (`ADR-0015`).
    """

    protocol: str = "PLAINTEXT"
    sasl_mechanism: str = ""
    sasl_username: str = ""
    sasl_password: str = ""
    #: Trust store for the broker's certificate. Empty uses the system trust store, which is what
    #: a broker with a publicly-issued certificate needs; a private CA is named here.
    ssl_cafile: str = ""

    @property
    def is_plaintext(self) -> bool:
        return self.protocol.strip().upper() in {"", "PLAINTEXT"}

    def client_kwargs(self) -> dict[str, Any]:
        """The keyword arguments both aiokafka clients take.

        Built here rather than at each call site: a producer that authenticates and a consumer
        that does not is a deployment where half the bus is protected, and the half that is not is
        the half that grants access.
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
    #: The W3C trace context of the request that **caused** this event, where one was captured.
    #:
    #: An outbox publishes from a different process, minutes later, with no span of its own — so
    #: the ambient context at publish time is empty and injecting it produced nothing at all. The
    #: causing request's context is stored on the outbox row and restored here, which is what makes
    #: a console change and the gateway applying it one trace (`FRD-615`). Empty for anything
    #: published outside a request, which is honest: there was no caller.
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

    **The client is built by an injected factory**, and that is not a testing nicety. `start()` is
    where a `security_protocol`, a SASL mechanism, a username, a trust store and a broker address
    are all first tested against reality at once, and it was three lines with no logging and a
    `# pragma: no cover` — so the one call in this system where a Kafka credential is proven had
    no unit test and said nothing when it failed. The factory lets the whole path be driven with a
    fake broker, which is what makes the `FRD-617` lines below a wire that is tested rather than
    two ends that both look right (`LESSONS.md` §1).
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
            # **Where it landed**, which `send_and_wait` has always returned and this discarded.
            # A partition and an offset are what somebody takes to `kafka-console-consumer` when
            # the far end says it never saw the event; without them the producer's word is the
            # only evidence that anything was published.
            call.note(
                partition=getattr(metadata, "partition", None),
                offset=getattr(metadata, "offset", None),
            )


def _aiokafka_producer(**kwargs: Any) -> Any:  # pragma: no cover - the real client, by name
    """The default factory. Imported here so aiokafka stays absent from a process that never
    produces — the management API imports this module for its topic names alone."""
    from aiokafka import AIOKafkaProducer

    return AIOKafkaProducer(**kwargs)
