"""Who may read a use case's stored content becomes one choice with three answers (`FRD-622`).

`restrict_members_to_own_requests` was a boolean, and a boolean cannot carry the third answer the
owner asked for: **stored, and readable by nobody inside the use case** — only by the installation
roles that may read any content. Two booleans would have spelled it, and would also have spelled
two combinations that mean nothing.

The old value is carried over rather than reset: `True` becomes `own`, `False` becomes `members`,
which is what each already meant.

**Every live use case is announced again.** The gateway builds its read-model from the compacted
topic, and the consumer fills an absent field with its default — so a use case that is not
re-announced would read as `members` on the gateway until somebody happened to edit it, which for
a use case that was restricted is a disclosure. A retired use case is not announced, because
announcing it would bring it back (as in `0015`).
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
        "payload_visibility": usecase.payload_visibility,
        # Derived, for a gateway that does not know the new field yet — see `views/payloads.py`.
        "restrict_members_to_own_requests": usecase.payload_visibility != "members",
        "tools_enabled": usecase.tools_enabled,
        "include_reasoning": usecase.include_reasoning,
        "prompt_caching_enabled": usecase.prompt_caching_enabled,
        "prompt_cache_ttl": usecase.prompt_cache_ttl,
        "retention_days": usecase.retention_days,
        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),
    }


def carry_the_restriction_over(apps: Any, schema_editor: Any) -> None:
    """`True` → `own`, `False` → `members`, which is what each already meant.

    Separate from the announcement below because the two are testable in different ways: this one
    reads a field the same migration removes, so it can only run against the *historical* model
    Django hands a migration — a test that called it with today's app registry would fail on the
    field's absence, which is a statement about the test and not about the migration.
    """
    use_case = apps.get_model("usecases", "UseCase")
    use_case.objects.filter(restrict_members_to_own_requests=True).update(payload_visibility="own")


def announce_every_live_use_case(apps: Any, schema_editor: Any) -> None:
    """Re-send each live use case in full, so the gateway's read-model learns the new field.

    Reads only fields that outlive this migration, which is what makes it checkable with today's
    models (`test_a_migration_announces_every_live_use_case.py`).
    """
    use_case = apps.get_model("usecases", "UseCase")
    outbox_event = apps.get_model("outbox", "OutboxEvent")
    topic = staged(USECASE_TOPIC, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")
    for usecase in use_case.objects.filter(deleted_at__isnull=True).order_by("slug"):
        outbox_event.objects.create(
            topic=topic,
            key=usecase.slug,
            event_type="usecase.upserted",
            payload=_snapshot(usecase),
            traceparent="",
        )


def restore_the_boolean(apps: Any, schema_editor: Any) -> None:
    """Backwards: anything but `members` was a restriction, which is what the boolean could say."""
    use_case = apps.get_model("usecases", "UseCase")
    use_case.objects.exclude(payload_visibility="members").update(
        restrict_members_to_own_requests=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0015_reasoning_on_by_default"),
        ("outbox", "0003_outboxevent_traceparent"),
    ]

    operations = [
        migrations.AddField(
            model_name="usecase",
            name="payload_visibility",
            field=models.CharField(
                choices=[
                    ("members", "Every member of the use case"),
                    ("own", "Administrators, and each person's own requests"),
                    ("oversight", "Global Administrators and IT Security only"),
                ],
                default="members",
                help_text=(
                    "Who inside this use case may read stored prompts and responses (FRD-622). "
                    "'members': everybody in the use case. 'own': administrators of the use case "
                    "read all of them, everybody else only their own requests. 'oversight': "
                    "nobody in the use case, not even its administrators — only the installation "
                    "roles that may read any content (Global Administrator, IT Security). The "
                    "default is the behaviour that already existed. Rows, figures and costs are "
                    "unaffected."
                ),
                max_length=16,
            ),
        ),
        migrations.RunPython(carry_the_restriction_over, restore_the_boolean),
        migrations.RemoveField(model_name="usecase", name="restrict_members_to_own_requests"),
        # After the removal: the snapshot is the one the views send **today**, and sending it
        # before the column is gone would describe a use case that still had it.
        migrations.RunPython(announce_every_live_use_case, migrations.RunPython.noop),
    ]
