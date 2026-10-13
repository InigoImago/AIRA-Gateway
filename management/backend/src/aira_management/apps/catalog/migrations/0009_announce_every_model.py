"""Every catalogued model is announced again, in full.

`0005` turned thinking levels from a token table into a list of words and `0006` turned a region
into a list of regions, both in this database only. The gateway keeps its own copy of each model,
built from the compacted topic, and reads `thinking.levels` **only as a list** — so a model
catalogued before `0005` declared no level words there until somebody happened to save it, and a
caller asking for `high` was refused as undeclared.

**A data migration that rewrites a declaration announces it**, the way `usecases/0016` announces
every live use case: the payload is the one `catalog.views._payload` sends, field for field,
because the consumer fills an absent field with its default and a partial announcement would reset
whatever it left out.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import migrations

from aira_common.kafka import MODEL_TOPIC, staged


def _price(value: Any) -> str | None:
    return str(value) if value is not None else None


def _payload(model: Any) -> dict[str, Any]:
    """The event `catalog.views._payload` sends, as the model stands at this migration."""
    return {
        "name": model.name,
        "display_name": model.display_name,
        "approved": model.approved,
        "provider": model.provider,
        "input_price_per_million": _price(model.input_price_per_million),
        "output_price_per_million": _price(model.output_price_per_million),
        "cached_input_price_per_million": _price(model.cached_input_price_per_million),
        "cache_write_price_per_million": _price(model.cache_write_price_per_million),
        "capabilities": list(model.capabilities or []),
        "publisher": model.publisher,
        "platform": model.platform,
        "addressing": model.addressing,
        "underlying_model": model.underlying_model,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "default_max_output_tokens": model.default_max_output_tokens,
        "thinking": model.thinking,
        "embedding": model.embedding,
        "attachments": model.attachments,
        "hosting": model.hosting,
        "deprecated": model.deprecated,
        "numeric_id": model.numeric_id,
    }


def announce_every_model(apps: Any, schema_editor: Any) -> None:
    """Re-send each model, so the gateway's copy takes the shapes `0005` and `0006` wrote.

    Reads only fields that outlive this migration, which is what makes it checkable with today's
    models (`test_a_catalog_migration_announces_every_model.py`).
    """
    model_class = apps.get_model("catalog", "Model")
    outbox_event = apps.get_model("outbox", "OutboxEvent")
    topic = staged(MODEL_TOPIC, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")
    for model in model_class.objects.order_by("name"):
        outbox_event.objects.create(
            topic=topic,
            key=model.name,
            event_type="model.upserted",
            payload=_payload(model),
            traceparent="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0008_approval_takes_catalog_write"),
        ("outbox", "0003_outboxevent_traceparent"),
    ]

    # Nothing to undo: an announcement describes the rows as they are, and a rolled-back catalogue
    # is announced by its next save.
    operations = [migrations.RunPython(announce_every_model, migrations.RunPython.noop)]
