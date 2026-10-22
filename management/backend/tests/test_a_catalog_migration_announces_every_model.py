"""A catalog data migration announces every model **in full** (`catalog/0009`).

`0005` and `0006` rewrote declarations in this database and announced nothing, so the gateway kept
the old shapes: it reads `thinking.levels` only as a list, and a model catalogued before `0005`
declared no level words there until somebody saved it again. The property, as for
`usecases/0016`: the consumer fills an absent field with its default, so an announcement is the
whole event the views send or it resets what it leaves out.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.views import _payload
from aira_management.apps.outbox.models import OutboxEvent
from django.apps import apps

pytestmark = pytest.mark.django_db

MIGRATION = importlib.import_module(
    "aira_management.apps.catalog.migrations.0009_announce_every_model"
)

#: Fields catalogued after `0009`, which it cannot read: the column does not exist when it runs.
#: Leaving them out resets nothing — when `0009` runs every row would hold the column's default,
#: which is also what the gateway holds for a model whose events never named the field.
ADDED_AFTER = frozenset({"fixed_sampling", "default_sampling"})


def _as_at_0009(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in ADDED_AFTER}


def test_every_model_is_announced_as_the_views_announce_it(settings) -> None:
    settings.AIRA_KAFKA_STAGE = "t"
    older = Model.objects.create(
        name="m-old",
        approved=True,
        input_price_per_million=Decimal("0.075"),
        output_price_per_million=Decimal("0.30"),
        thinking={"modes": ["auto"], "levels": ["low", "high"]},
        addressing={"regions": ["europe-west4"]},
    )
    unapproved = Model.objects.create(name="m-new")
    OutboxEvent.objects.all().delete()

    MIGRATION.announce_every_model(apps, None)

    events = list(OutboxEvent.objects.order_by("id"))
    assert [(e.topic, e.key, e.event_type) for e in events] == [
        ("aira.t.models", "m-new", "model.upserted"),
        ("aira.t.models", "m-old", "model.upserted"),
    ]
    # Field for field what the views send: a partial event would reset what it leaves out.
    unapproved.refresh_from_db()
    older.refresh_from_db()
    assert events[0].payload == _as_at_0009(_payload(unapproved))
    assert events[1].payload == _as_at_0009(_payload(older))
    assert not ADDED_AFTER & events[0].payload.keys()
    # And the shape the gateway reads levels in.
    assert events[1].payload["thinking"]["levels"] == ["low", "high"]
