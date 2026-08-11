"""Carry every `allow_check` list into the use case's model release (`FRD-308`).

The pipeline step this replaces was a **decision somebody made**: they opened the builder, added
the step and typed model names. Dropping it would delete that decision, and the use case would go
from "these three models" to "none" without anybody choosing it.

What is deliberately **not** done here is filling in a list for a use case that never had the step.
Before today those use cases could call every approved model, so a faithful migration would write
the whole approved catalog into each of them — and that would be a release nobody decided, shown in
the console as though somebody had. `FRD-122`'s rule about the audit row applies to configuration
too: an unverifiable claim is not evidence. Those use cases start empty and stop serving, which is
what the owner chose knowing the consequence, and every refusal names the model, the use case and
who can release it.

Idempotent and reversible-by-doing-nothing: the reverse leaves the releases in place, because the
pipeline step is gone by then and un-releasing would be a second unmade decision.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations


def _release_what_allow_check_permitted(apps: Any, schema_editor: Any) -> None:
    UseCase = apps.get_model("usecases", "UseCase")
    Model = apps.get_model("catalog", "Model")
    PipelineConfig = apps.get_model("pipelines", "PipelineConfig")

    for config in PipelineConfig.objects.select_related("use_case").all():
        names: list[str] = []
        for step in config.steps or []:
            if isinstance(step, dict) and step.get("type") == "allow_check":
                listed = (step.get("config") or {}).get("models") or []
                names.extend(str(name) for name in listed if name)
        if not names:
            continue
        try:
            usecase = UseCase.objects.get(pk=config.use_case_id)
        except UseCase.DoesNotExist:  # pragma: no cover — FK makes this unreachable
            continue
        # Only names the catalog actually has. A step could name a model that was never
        # catalogued — it would have been refused at dispatch anyway (`FRD-307`), and carrying it
        # into a release would put a name in the console that can never work.
        usecase.allowed_models.add(*Model.objects.filter(name__in=set(names)))


def _drop_the_step(apps: Any, schema_editor: Any) -> None:
    """Remove `allow_check` from every stored pipeline, now that the release enforces it.

    Left behind, the step would be a second answer to "which models may this use case use" — and
    the console no longer offers a way to see or edit it, so the one that disagreed would be the
    invisible one.
    """
    PipelineConfig = apps.get_model("pipelines", "PipelineConfig")
    for config in PipelineConfig.objects.all():
        kept = [
            step
            for step in (config.steps or [])
            if not (isinstance(step, dict) and step.get("type") == "allow_check")
        ]
        if len(kept) != len(config.steps or []):
            config.steps = kept
            config.save(update_fields=["steps"])


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0009_usecase_allowed_models"),
        ("catalog", "0001_initial"),
        ("pipelines", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_release_what_allow_check_permitted, migrations.RunPython.noop),
        migrations.RunPython(_drop_the_step, migrations.RunPython.noop),
    ]
