"""A data migration announces every live use case in **full**, and skips the retired ones.

**This file was written for `0015` and now tests `0016`**, for a reason worth keeping: it calls a
migration's own function against *today's* models, and `0015`'s snapshot reads a field that `0016`
removes (`restrict_members_to_own_requests`). The moment the schema moved past it, the test could
no longer run — not because the migration is wrong, but because Django hands a real migration the
**historical** model and a test calling the function directly cannot.

That is also why `0016` has two `RunPython` steps instead of one. The carry-over reads the field it
removes, so it is only meaningful against history and is checked here by **running the migrations**
backwards and forwards on the test database, the way an upgrade does. The announcement reads only
fields that outlive the migration, so it can be called directly.

The property both halves serve: the gateway builds its read-model from a compacted topic, and the
consumer fills an absent field with its default. A partial announcement therefore **resets**
whatever it leaves out, and a retired use case announced at all comes back to life.
"""

from __future__ import annotations

import importlib

import pytest
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views.payloads import _snapshot
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

pytestmark = pytest.mark.django_db

MIGRATION = importlib.import_module(
    "aira_management.apps.usecases.migrations.0016_payload_visibility"
)


def test_every_live_use_case_is_announced_in_full(settings) -> None:
    settings.AIRA_KAFKA_STAGE = "t"
    live = UseCase.objects.create(slug="live", name="Live")
    UseCase.objects.create(slug="retired", name="Retired", deleted_at=timezone.now())
    OutboxEvent.objects.all().delete()

    MIGRATION.announce_every_live_use_case(apps, None)

    events = list(OutboxEvent.objects.all())
    # The retired use case is not announced: an upsert would bring it back in the gateway.
    assert [(e.topic, e.key, e.event_type) for e in events] == [
        ("aira.t.usecases", "live", "usecase.upserted")
    ]
    # The event the views send, field for field: a partial one would reset what it leaves out.
    assert events[0].payload == _snapshot(live)


def test_the_announcement_carries_the_new_field_and_the_one_an_old_gateway_reads(settings) -> None:
    """Both, because the announcement exists for the instances that have **not** been upgraded yet
    as much as for the ones that have (`FRD-204`): the new field for a gateway that understands it,
    the derived boolean for one that does not."""
    settings.AIRA_KAFKA_STAGE = "t"
    UseCase.objects.create(slug="hidden", name="H", payload_visibility="oversight")
    OutboxEvent.objects.all().delete()

    MIGRATION.announce_every_live_use_case(apps, None)

    payload = OutboxEvent.objects.get().payload
    assert payload["payload_visibility"] == "oversight"
    assert payload["restrict_members_to_own_requests"] is True


@pytest.mark.django_db(transaction=True)
def test_a_restriction_survives_the_upgrade_that_replaces_its_field() -> None:
    """**The upgrade, run.** Backwards to the state before this migration, a restricted use case
    written the way the old schema wrote one, then forwards.

    This is the only half of the migration no unit test can reach, and it is the half that cannot
    be repaired afterwards: the boolean is dropped in the same migration, so a use case whose
    restriction was not translated here would read as *readable by every member* and nothing would
    ever say otherwise. Slow (it migrates a database twice) and worth it once.
    """
    call_command("migrate", "usecases", "0015", verbosity=0)
    insert = (
        "INSERT INTO usecases_usecase "
        "(slug, name, description, processing_notes, deleted_by, store_payloads, tools_enabled, "
        " prompt_caching_enabled, include_reasoning, prompt_cache_ttl, "
        " restrict_members_to_own_requests, retention_days, created_at, updated_at) "
        "VALUES (%s, %s, '', '', '', true, false, false, true, '5m', %s, 7, "
        "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    with connection.cursor() as cursor:
        cursor.execute(insert, ["restricted", "R", True])
        cursor.execute(insert, ["open", "O", False])

    call_command("migrate", "usecases", verbosity=0)

    visibilities = dict(UseCase.objects.values_list("slug", "payload_visibility"))
    assert visibilities == {"restricted": "own", "open": "members"}


@pytest.mark.django_db(transaction=True)
def test_reasoning_was_switched_on_for_the_use_cases_that_predate_it() -> None:
    """`0015`'s data effect, by the only route still open to it (`FRD-135`).

    Its test used to call `turn_reasoning_on` with today's models, which stopped being possible the
    moment `0016` removed a field its snapshot reads. The effect is still worth guarding — a fresh
    installation runs this migration too, and an upgrade runs it over real use cases — so it is
    checked the same way as the carry-over above: migrate back to the state before it, write a use
    case the way that schema wrote one, and migrate forward.
    """
    call_command("migrate", "usecases", "0014", verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO usecases_usecase "
            "(slug, name, description, processing_notes, deleted_by, store_payloads, "
            " tools_enabled, prompt_caching_enabled, include_reasoning, prompt_cache_ttl, "
            " restrict_members_to_own_requests, retention_days, created_at, updated_at) "
            "VALUES ('older', 'O', '', '', '', true, false, false, false, '5m', false, 7, "
            "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    call_command("migrate", "usecases", verbosity=0)

    assert UseCase.objects.get(slug="older").include_reasoning is True
