from aira_common.kafka import (
    EVENT_TYPE_HEADER,
    USECASE_TOPIC,
    AiokafkaProducer,
    InMemoryProducer,
    KafkaRecord,
    KafkaSecurity,
)


async def test_in_memory_producer_records() -> None:
    producer = InMemoryProducer()
    await producer.start()
    record = KafkaRecord(
        topic=USECASE_TOPIC, key="uc", event_type="usecase.upserted", payload={"slug": "uc"}
    )
    await producer.send(record)
    await producer.stop()
    assert producer.sent == [record]


# ---- the bus is a trust boundary (2026-08-09) --------------------------------------------


def test_plaintext_is_the_default_and_says_so() -> None:
    """The Compose stack and every laptop run on it, so the default cannot change — what changes
    is that a deployment can now be *asked* about it, and is refused outside `local`."""
    assert KafkaSecurity().is_plaintext is True
    assert KafkaSecurity().client_kwargs() == {"security_protocol": "PLAINTEXT"}


def test_sasl_credentials_reach_the_client() -> None:
    """The property that matters is not the dict's shape but that a configured identity is
    actually passed: a producer built without these connects anonymously and succeeds, which is
    the failure that looks like success."""
    kwargs = KafkaSecurity(
        protocol="SASL_SSL",
        sasl_mechanism="scram-sha-512",
        sasl_username="aira-relay",
        sasl_password="s3cret",
    ).client_kwargs()

    assert kwargs["security_protocol"] == "SASL_SSL"
    assert kwargs["sasl_mechanism"] == "SCRAM-SHA-512"
    assert kwargs["sasl_plain_username"] == "aira-relay"
    assert kwargs["sasl_plain_password"] == "s3cret"
    assert "ssl_context" in kwargs


def test_a_mechanism_is_chosen_rather_than_left_absent() -> None:
    """aiokafka defaults `sasl_mechanism` to PLAIN, which sends the password in the clear over a
    SASL_PLAINTEXT connection. Naming SCRAM when the operator did not is the safe direction."""
    kwargs = KafkaSecurity(protocol="SASL_PLAINTEXT", sasl_username="u").client_kwargs()

    assert kwargs["sasl_mechanism"] == "SCRAM-SHA-512"


def test_ssl_without_sasl_still_builds_a_context_and_no_credentials() -> None:
    kwargs = KafkaSecurity(protocol="SSL").client_kwargs()

    assert "ssl_context" in kwargs
    assert "sasl_plain_username" not in kwargs


def test_the_protocol_is_read_case_insensitively() -> None:
    """An operator writing `sasl_ssl` in an env file has configured TLS, not a typo that silently
    falls back to plaintext."""
    assert KafkaSecurity(protocol="sasl_ssl").is_plaintext is False
    assert KafkaSecurity(protocol="sasl_ssl").client_kwargs()["security_protocol"] == "SASL_SSL"
    assert KafkaSecurity(protocol="  PLAINTEXT  ").is_plaintext is True


async def test_the_producer_publishes_under_the_stored_context_not_its_own() -> None:
    """The line that actually goes on the wire, driven rather than described (`FRD-615`).

    `kafka_headers_for` has its own tests and they pass whether or not the producer calls it with
    the record's context — which `make mutants` reported the moment the mutation was written:
    replacing `kafka_headers_for(record.traceparent)` with `kafka_headers_for()` broke nothing,
    because every test stopped one call short of the wire. `send` is `# pragma: no cover` for its
    aiokafka I/O; the header construction above it is ours, and this is where it is checked.

    The relay is given a span of its own here on purpose: that is the case where the two answers
    differ, and publishing under the **publisher's** trace instead of the request's is exactly the
    silent version of the defect this closed.
    """
    from opentelemetry.sdk.trace import TracerProvider

    class _Broker:
        def __init__(self) -> None:
            self.headers: list[tuple[str, bytes]] = []

        async def send_and_wait(self, topic, value, key, headers):  # noqa: ANN001, ARG002
            self.headers = headers

    stored = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    producer = AiokafkaProducer("localhost:9092")
    broker = _Broker()
    producer._producer = broker  # noqa: SLF001 — the transport, replaced; the code under test is real

    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("relay-loop"):
        await producer.send(
            KafkaRecord(
                topic=USECASE_TOPIC,
                key="uc-a",
                event_type="usecase.upserted",
                payload={"slug": "uc-a"},
                traceparent=stored,
            )
        )

    sent = dict(broker.headers)
    assert sent["traceparent"].decode() == stored
    assert sent[EVENT_TYPE_HEADER].decode() == "usecase.upserted"
