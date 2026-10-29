"""What a use case does with the installation's fallback chains (`FRD-310`).

Every pipeline keeps doing what it did: one that names a chain of its own goes on using it
(`own`), and one that names none follows the installation's chains (`default`) — which are empty
until somebody sets one, so nothing falls back that did not before. The gateway reads a pipeline
event without the field the same way (`aira_common.fallback.stored_mode`), so nothing is announced.
"""

from typing import Any

from django.db import migrations, models


def keep_what_each_pipeline_did(apps: Any, schema_editor: Any) -> None:
    PipelineConfig = apps.get_model("pipelines", "PipelineConfig")
    for config in PipelineConfig.objects.all():
        if config.fallback_models:
            config.fallback_mode = "own"
            config.save(update_fields=["fallback_mode"])


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0003_remove_pipelineconfig_start_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelineconfig",
            name="fallback_mode",
            field=models.CharField(
                choices=[("default", "default"), ("own", "own"), ("none", "none")],
                default="default",
                max_length=16,
            ),
        ),
        migrations.RunPython(keep_what_each_pipeline_did, migrations.RunPython.noop),
    ]
