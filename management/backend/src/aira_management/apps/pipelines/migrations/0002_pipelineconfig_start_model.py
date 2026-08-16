"""Where a request enters a pipeline when the caller names no model (`ADR-0020`).

Blank for every existing pipeline, and blank is a real state rather than a gap to backfill: it
means *this pipeline is only ever entered by a caller who names a model*, which is every ordinary
API request and is exactly what every pipeline written before today was for. Guessing one — the
first released model, say — would write a decision nobody made into a field the console then shows
as one.

The consequence of leaving it blank is bounded and visible: the question catalogue (`FRD-504`)
cannot run against that use case, and says why.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelineconfig",
            name="start_model",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
