from io import StringIO

import aira_management.apps.outbox.management.commands.relay as relay
import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.outbox.subscriber import EventTooLarge, UnroutedEvent, record_to_outbox
from django.core.management import call_command
from django.core.management.base import CommandError

from aira_common.kafka import (
    MAX_EVENT_BYTES,
    MEMBERSHIP_TOPIC,
    USECASE_TOPIC,
    AiokafkaProducer,
    InMemoryProducer,
    KafkaRecord,
    RecordRefused,
)

pytestmark = pytest.mark.django_db


def test_record_to_outbox_writes_row() -> None:
    record_to_outbox("usecase.upserted", {"slug": "uc", "name": "N"})
    row = OutboxEvent.objects.get()
    assert row.topic == USECASE_TOPIC
    assert row.event_type == "usecase.upserted"
    assert row.key == "uc"
    assert str(row)  # __str__ smoke


def test_an_event_no_topic_carries_is_refused_loudly() -> None:
    """A silent return wrote such a change nowhere: it reached the console and never the gateway,
    and nothing anywhere said so. An unknown input is an error, not a no-op."""
    with pytest.raises(UnroutedEvent, match="unknown.event"):
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


# ---- an event the channel cannot carry is refused with the change ---------------------------


def test_an_event_larger_than_the_channel_carries_is_refused_and_writes_nothing() -> None:
    """Measured as the producer encodes it — `json.dumps` escapes `€` into six bytes — against the
    shared bound, so what is accepted here is what the producer sends."""
    payload = {"slug": "uc", "description": "€" * (MAX_EVENT_BYTES // 6 + 1)}

    with pytest.raises(EventTooLarge) as refused:
        record_to_outbox("usecase.upserted", payload)

    assert "usecase.upserted" in str(refused.value.detail)
    assert OutboxEvent.objects.count() == 0


def test_a_change_whose_event_is_too_large_is_rolled_back_with_a_400(monkeypatch) -> None:
    """Raised inside the view's transaction, so the change goes with its event: a `200` here was a
    change the console showed as saved and the relay could never send."""
    from aira_management.apps.outbox import subscriber
    from aira_management.apps.usecases.models import UseCase
    from aira_management.rbac import sync_user_roles
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    from .conftest import role_claims

    root = get_user_model().objects.create(username="root")
    sync_user_roles(root, role_claims("global-admin"))
    client = APIClient()
    client.force_authenticate(user=root)
    assert (
        client.post("/api/v1/use-cases/", {"slug": "uc", "name": "UC"}, format="json").status_code
        == 201
    )
    before = OutboxEvent.objects.count()
    monkeypatch.setattr(subscriber, "MAX_EVENT_BYTES", 400)

    response = client.patch("/api/v1/use-cases/uc/", {"description": "x" * 500}, format="json")

    assert response.status_code == 400, response.content
    assert "cannot be passed on to the gateway" in response.json()["error"]["message"]
    assert UseCase.objects.get(slug="uc").description == ""
    assert OutboxEvent.objects.count() == before


# ---- the relay ------------------------------------------------------------------------------


def _three_events() -> list[OutboxEvent]:
    return [
        OutboxEvent.objects.create(
            topic=USECASE_TOPIC, key=f"uc-{n}", event_type="usecase.upserted", payload={"n": n}
        )
        for n in range(3)
    ]


class _FailsAt(InMemoryProducer):
    """A broker that answers the record keyed ``key`` with ``error``, and takes everything else."""

    def __init__(self, key: str, error: Exception) -> None:
        super().__init__()
        self._key, self._error = key, error

    async def send(self, record: KafkaRecord) -> None:
        if record.key == self._key:
            raise self._error
        await super().send(record)


def test_what_was_sent_before_a_failure_is_marked_and_not_sent_again(monkeypatch) -> None:
    """The run threw away the ids it had sent the moment a later send failed, so every run sent
    everything ahead of the failing event again. Order is meaning, so it still stops there."""
    first, second, third = _three_events()
    broker = _FailsAt("uc-1", ConnectionError("broker went away"))
    monkeypatch.setattr(relay, "build_producer", lambda: broker)

    with pytest.raises(CommandError, match=f"stopped at event {second.pk}"):
        call_command("relay", stdout=StringIO())

    assert [record.key for record in broker.sent] == ["uc-0"]
    assert OutboxEvent.objects.get(pk=first.pk).published_at is not None
    assert {e.pk for e in OutboxEvent.objects.pending()} == {second.pk, third.pk}

    healed = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: healed)
    call_command("relay", stdout=StringIO())
    assert [record.key for record in healed.sent] == ["uc-1", "uc-2"], "nothing sent twice"


def test_an_event_the_broker_will_never_take_is_parked_and_the_queue_moves_on(monkeypatch) -> None:
    """Retried at the head of the queue, one such event held back every later change of the
    installation — revocations included — on every run, for good."""
    first, second, third = _three_events()
    broker = _FailsAt("uc-1", RecordRefused("MessageSizeTooLargeError"))
    monkeypatch.setattr(relay, "build_producer", lambda: broker)
    err = StringIO()

    call_command("relay", stdout=StringIO(), stderr=err)

    assert [record.key for record in broker.sent] == ["uc-0", "uc-2"]
    parked = OutboxEvent.objects.get(pk=second.pk)
    assert parked.parked_at is not None and parked.published_at is None
    assert "refused it for good" in parked.parked_reason
    assert f"parked: event {second.pk}" in err.getvalue()
    assert not OutboxEvent.objects.pending().exists()


def test_an_event_already_too_large_is_parked_without_being_sent(monkeypatch) -> None:
    """A row written before the write-time bound existed, or by a writer that skipped it: measured
    by the producer's own encoding and set aside before it reaches the broker."""
    big = OutboxEvent.objects.create(
        topic=USECASE_TOPIC,
        key="uc-big",
        event_type="usecase.upserted",
        payload={"description": "€" * (MAX_EVENT_BYTES // 6 + 1)},
    )
    after = OutboxEvent.objects.create(
        topic=USECASE_TOPIC, key="uc-after", event_type="usecase.upserted", payload={}
    )
    broker = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: broker)

    call_command("relay", stdout=StringIO(), stderr=StringIO())

    assert [record.key for record in broker.sent] == ["uc-after"]
    assert "the event channel carries at most" in OutboxEvent.objects.get(pk=big.pk).parked_reason
    assert OutboxEvent.objects.get(pk=after.pk).published_at is not None


def test_a_second_relay_run_leaves_the_outbox_to_the_first(monkeypatch) -> None:
    """Two runs at once can publish one key's events in the wrong order, and on a compacted topic
    the older one survives. The run that cannot take the lock touches nothing."""
    from contextlib import contextmanager

    _three_events()
    broker = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: broker)

    @contextmanager
    def held():
        yield False

    monkeypatch.setattr(relay, "exclusive_run", held)
    out = StringIO()

    call_command("relay", stdout=out)

    assert broker.sent == []
    assert "another relay run holds the outbox" in out.getvalue()
    assert OutboxEvent.objects.pending().count() == 3


def test_what_was_delivered_is_kept_for_a_while_and_then_deleted(monkeypatch) -> None:
    """A delivered row is a copy of what the gateway already has, names included; kept for
    `KEPT_AFTER_DAYS` so what was sent can be compared with what arrived, and not for ever."""
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    old_published, recent, old_parked, pending = (
        OutboxEvent.objects.create(
            topic=USECASE_TOPIC, key=k, event_type="usecase.upserted", payload={}
        )
        for k in ("old", "recent", "old-parked", "pending")
    )
    past = now - timedelta(days=relay.KEPT_AFTER_DAYS + 1)
    OutboxEvent.objects.filter(pk=old_published.pk).update(published_at=past)
    OutboxEvent.objects.filter(pk=recent.pk).update(published_at=now - timedelta(days=1))
    OutboxEvent.objects.filter(pk=old_parked.pk).update(parked_at=past, parked_reason="too big")

    assert relay.sweep(now) == 2

    assert set(OutboxEvent.objects.values_list("key", flat=True)) == {"recent", "pending"}
