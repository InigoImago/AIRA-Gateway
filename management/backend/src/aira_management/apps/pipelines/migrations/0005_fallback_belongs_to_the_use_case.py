"""A use case keeps its own fallback chain for each kind of request; the installation keeps none
(`FRD-310`, `ADR-0052`).

The installation's chains (`catalog.FallbackChain`) and a pipeline's `fallback_mode` go, and a
pipeline gains a chain for embeddings and one for rankings beside the one for text generation.

**Every use case keeps doing what it did.** Each chain the installation set is written into every
live use case that followed it — under `default` every kind, under `own` embedding and ranking,
since `own` kept a chain of its own for text generation only — as far as the use case was released
the model and the model can serve the kind, which is as far as the gateway would have tried it. A
pipeline under `none` keeps empty chains. Each pipeline this changes is announced in full, as the
views send it, because the gateway reads an absent chain as empty. An installation that set no
chain — the default — changes nothing here.

The kinds, their fields and what serves each are written out as they stand at this migration
rather than imported, so a later change to the rule does not rewrite what this one did.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import migrations, models

from aira_common.kafka import PIPELINE_TOPIC, staged

#: The pipeline field each kind's chain is written to.
FIELDS = {
    "generate": "fallback_models",
    "embed": "embedding_fallback_models",
    "rerank": "ranking_fallback_models",
}
#: What a model must declare to stand in for each kind; an undeclared model has the baseline.
SERVES = {"generate": {"generate", "speech"}, "embed": {"embed"}, "rerank": {"rerank"}}
BASELINE = {"generate", "embed"}
#: The kinds a use case followed the installation for, under each mode.
FOLLOWED = {"default": ("generate", "embed", "rerank"), "own": ("embed", "rerank"), "none": ()}


def _payload(slug: str, config: Any) -> dict[str, Any]:
    """The event `usecases/views/payloads.py` sends, as the pipeline stands at this migration."""
    return {
        "use_case": slug,
        "steps": config.steps,
        **{field: list(getattr(config, field) or []) for field in FIELDS.values()},
    }


def _declared(capabilities: Any) -> set[str]:
    words = {str(word) for word in capabilities or [] if isinstance(word, str)}
    return words or BASELINE


def carry_the_installation_chains_into_each_use_case(apps: Any, schema_editor: Any) -> None:
    chains = {
        row.kind: [name for name in row.chain or [] if isinstance(name, str) and name]
        for row in apps.get_model("catalog", "FallbackChain").objects.all()
    }
    if not any(chains.values()):
        return
    declared = {
        model.name: _declared(model.capabilities)
        for model in apps.get_model("catalog", "Model").objects.all()
    }
    pipeline_class = apps.get_model("pipelines", "PipelineConfig")
    outbox_event = apps.get_model("outbox", "OutboxEvent")
    topic = staged(PIPELINE_TOPIC, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")
    live = apps.get_model("usecases", "UseCase").objects.filter(deleted_at__isnull=True)
    for use_case in live.order_by("slug"):
        config = pipeline_class.objects.filter(use_case=use_case).first()
        mode = config.fallback_mode if config is not None else "default"
        released = set(use_case.allowed_models.values_list("name", flat=True))
        carried = {
            FIELDS[kind]: [
                name
                for name in chains.get(kind, [])
                if name in released and declared.get(name, set()) & SERVES[kind]
            ]
            for kind in FOLLOWED.get(mode, ())
        }
        carried = {field: names for field, names in carried.items() if names}
        if not carried:
            continue
        if config is None:
            config = pipeline_class(use_case=use_case, steps=[], fallback_models=[])
        for field, names in carried.items():
            setattr(config, field, names)
        config.save()
        outbox_event.objects.create(
            topic=topic,
            key=use_case.slug,
            event_type="pipeline.upserted",
            payload=_payload(use_case.slug, config),
            traceparent="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0004_fallback_mode"),
        ("catalog", "0013_fallback_chains"),
        ("usecases", "0019_mail_on_budgets_and_anomalies"),
        ("outbox", "0004_parked_events_and_a_pending_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelineconfig",
            name="embedding_fallback_models",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="pipelineconfig",
            name="ranking_fallback_models",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(
            carry_the_installation_chains_into_each_use_case, migrations.RunPython.noop
        ),
        migrations.RemoveField(model_name="pipelineconfig", name="fallback_mode"),
    ]
