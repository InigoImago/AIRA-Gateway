"""Reasoning is on for every use case unless an administrator turns it off (`FRD-135`).

The default changes, and every existing use case changes with it: a stored `false` cannot say
whether it was chosen or inherited.

The gateway builds its read model from the compacted topic, so each live use case is announced
again with a **complete** `usecase.upserted`. A partial event would reset what it leaves out: the
consumer overwrites every field and fills an absent one with its default, which for the released
models means unrestricted. A retired use case is not announced, because announcing it would bring
it back.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import migrations, models

from aira_common.kafka import USECASE_TOPIC, staged


def _snapshot(usecase: Any) -> dict[str, Any]:
    """The event `views/payloads.py` sends, as the use case stands at this migration."""
    return {
        "slug": usecase.slug,
        "name": usecase.name,
        "description": usecase.description,
        "processing_notes": usecase.processing_notes,
        "store_payloads": usecase.store_payloads,
        "restrict_members_to_own_requests": usecase.restrict_members_to_own_requests,
        "tools_enabled": usecase.tools_enabled,
        "include_reasoning": usecase.include_reasoning,
        "prompt_caching_enabled": usecase.prompt_caching_enabled,
        "prompt_cache_ttl": usecase.prompt_cache_ttl,
        "retention_days": usecase.retention_days,
        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),
    }


def turn_reasoning_on(apps: Any, schema_editor: Any) -> None:
    use_case = apps.get_model("usecases", "UseCase")
    outbox_event = apps.get_model("outbox", "OutboxEvent")
    use_case.objects.update(include_reasoning=True)
    topic = staged(USECASE_TOPIC, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")
    for usecase in use_case.objects.filter(deleted_at__isnull=True).order_by("slug"):
        outbox_event.objects.create(
            topic=topic,
            key=usecase.slug,
            event_type="usecase.upserted",
            payload=_snapshot(usecase),
            traceparent="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0014_alter_usecase_slug"),
        ("outbox", "0003_outboxevent_traceparent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usecase",
            name="include_reasoning",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Return the model's reasoning to callers and store it with the answer "
                    "(FRD-135). On by default; turn it off where reasoning must not be kept, "
                    "since it can restate the prompt verbatim."
                ),
            ),
        ),
        migrations.RunPython(turn_reasoning_on, migrations.RunPython.noop),
    ]
