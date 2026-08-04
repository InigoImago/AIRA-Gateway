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


def test_relay_no_pending_events() -> None:
    out = StringIO()
    call_command("relay", stdout=out)
    assert "no pending events" in out.getvalue()


def test_build_producer_returns_aiokafka_producer() -> None:
    assert isinstance(relay.build_producer(), AiokafkaProducer)
