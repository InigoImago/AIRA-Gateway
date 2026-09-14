"""Management publishes to the topics of its stage (`aira.t.usecases`), the ones the gateway of the
same stage subscribes to. Without a stage the names are today's."""

from __future__ import annotations

import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.outbox.subscriber import record_to_outbox
from aira_management.config.runtime import get_settings
from pydantic import ValidationError

pytestmark = pytest.mark.django_db


def test_an_event_travels_on_the_topic_of_this_stage(settings) -> None:
    settings.AIRA_KAFKA_STAGE = "t"
    record_to_outbox("usecase.upserted", {"slug": "a"})
    record_to_outbox("role.upserted", {"slug": "controlling"})

    assert sorted(OutboxEvent.objects.values_list("topic", flat=True)) == [
        "aira.t.roles",
        "aira.t.usecases",
    ]


def test_without_a_stage_the_topic_is_todays(settings) -> None:
    settings.AIRA_KAFKA_STAGE = ""
    record_to_outbox("usecase.upserted", {"slug": "a"})

    assert OutboxEvent.objects.get().topic == "aira.usecases"


def test_a_stage_that_cannot_be_a_name_segment_refuses_the_settings() -> None:
    with pytest.raises(ValidationError):
        type(get_settings())(kafka_stage="T.1")
