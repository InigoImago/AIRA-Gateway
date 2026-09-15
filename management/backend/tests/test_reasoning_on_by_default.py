"""Every live use case returns reasoning after the migration, and the gateway hears of each one in
full (`FRD-135`)."""

from __future__ import annotations

import importlib

import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views.payloads import _snapshot
from django.apps import apps
from django.utils import timezone

pytestmark = pytest.mark.django_db

MIGRATION = importlib.import_module(
    "aira_management.apps.usecases.migrations.0015_reasoning_on_by_default"
)


def test_every_live_use_case_is_switched_on_and_announced_in_full(settings) -> None:
    settings.AIRA_KAFKA_STAGE = "t"
    live = UseCase.objects.create(slug="live", name="Live", include_reasoning=False)
    UseCase.objects.create(
        slug="retired", name="Retired", include_reasoning=False, deleted_at=timezone.now()
    )
    OutboxEvent.objects.all().delete()

    MIGRATION.turn_reasoning_on(apps, None)

    live.refresh_from_db()
    assert live.include_reasoning is True
    events = list(OutboxEvent.objects.all())
    # The retired use case is not announced: an upsert would bring it back in the gateway.
    assert [(e.topic, e.key, e.event_type) for e in events] == [
        ("aira.t.usecases", "live", "usecase.upserted")
    ]
    # The event the views send, field for field: a partial one would reset what it leaves out.
    assert events[0].payload == _snapshot(live)
