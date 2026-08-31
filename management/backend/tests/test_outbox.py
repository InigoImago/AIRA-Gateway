from io import StringIO

import aira_management.apps.outbox.management.commands.relay as relay
import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.outbox.subscriber import record_to_outbox
from django.core.management import call_command

from aira_common.kafka import MEMBERSHIP_TOPIC, USECASE_TOPIC, AiokafkaProducer, InMemoryProducer

pytestmark = pytest.mark.django_db


def test_record_to_outbox_writes_row() -> None:
    record_to_outbox("usecase.upserted", {"slug": "uc", "name": "N"})
    row = OutboxEvent.objects.get()
    assert row.topic == USECASE_TOPIC
    assert row.event_type == "usecase.upserted"
    assert row.key == "uc"
    assert str(row)  # __str__ smoke


def test_record_to_outbox_ignores_unknown_event() -> None:
    record_to_outbox("unknown.event", {})
    assert OutboxEvent.objects.count() == 0


def test_relay_publishes_and_marks(monkeypatch) -> None:
    OutboxEvent.objects.create(
        topic=USECASE_TOPIC, key="uc", event_type="usecase.upserted", payload={"slug": "uc"}
    )
    OutboxEvent.objects.create(
        topic=MEMBERSHIP_TOPIC,
        key="uc",
        event_type="membership.upserted",
        payload={"slug": "uc", "username": "a"},
    )
    fake = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: fake)

    out = StringIO()
    call_command("relay", stdout=out)

    assert len(fake.sent) == 2
    assert OutboxEvent.objects.filter(published_at__isnull=True).count() == 0
    assert "published 2" in out.getvalue()


def test_relay_publishes_one_batch_and_says_what_is_left(monkeypatch) -> None:
    """**Bounded, and loud about it.**

    The query had no limit, so a relay coming back after an outage loaded every pending row into
    memory at once, each with a full JSON payload — the failure `RequestLogWriter` already reasons
    about on the other plane (*"an unbounded one would only move the exhaustion it is meant to
    prevent from the connection pool to memory"*), here without the reasoning.

    Nothing is lost: the relay runs on a loop, so the rest goes out on the next pass, **in order** —
    a slice of a queryset ordered by `(created_at, id)` is the oldest events and never a sample.
    And it says how many are left, because a bounded run that stays silent reads as "the outbox is
    empty" to whoever runs it.
    """
    monkeypatch.setattr(relay, "BATCH", 2)
    for index in range(5):
        OutboxEvent.objects.create(
            topic=USECASE_TOPIC,
            key=f"uc-{index}",
            event_type="usecase.upserted",
            payload={"slug": f"uc-{index}"},
        )
    fake = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: fake)

    out = StringIO()
    call_command("relay", stdout=out)

    assert [record.key for record in fake.sent] == ["uc-0", "uc-1"], "the oldest two, in order"
    assert OutboxEvent.objects.filter(published_at__isnull=True).count() == 3
    assert "published 2" in out.getvalue()
    assert "3 still pending" in out.getvalue()


def test_relay_says_nothing_about_a_backlog_when_there_is_none(monkeypatch) -> None:
    """The ordinary run stays as it reads today — a note about zero remaining events is noise."""
    OutboxEvent.objects.create(
        topic=USECASE_TOPIC, key="uc", event_type="usecase.upserted", payload={"slug": "uc"}
    )
    monkeypatch.setattr(relay, "build_producer", InMemoryProducer)

    out = StringIO()
    call_command("relay", stdout=out)

    assert "published 1" in out.getvalue()
    assert "pending" not in out.getvalue()


def test_relay_no_pending_events() -> None:
    out = StringIO()
    call_command("relay", stdout=out)
    assert "no pending events" in out.getvalue()


def test_build_producer_returns_aiokafka_producer() -> None:
    assert isinstance(relay.build_producer(), AiokafkaProducer)


def test_an_event_carries_the_trace_of_the_request_that_caused_it() -> None:
    """`FRD-615`. An outbox breaks the causal chain on purpose — the row is written inside the
    request and published by another process minutes later — so the context has to be *stored*
    here or there is nothing left to publish it under. Reading the ambient context at publish time
    is what the producer did, and a publisher has no span."""
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("PATCH /api/v1/use-cases/uc-a"):
        record_to_outbox("usecase.upserted", {"slug": "uc-a"})

    row = OutboxEvent.objects.get(event_type="usecase.upserted")
    assert row.traceparent.startswith("00-")


def test_an_event_with_no_request_behind_it_carries_no_trace() -> None:
    """The seed and every management command. Blank is the honest answer — there was no caller —
    and the consumer gives such a message a trace of its own rather than dropping it."""
    record_to_outbox("usecase.upserted", {"slug": "uc-b"})
    assert OutboxEvent.objects.get(event_type="usecase.upserted").traceparent == ""
