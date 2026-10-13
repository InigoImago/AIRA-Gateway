"""A use case's description and processing notes are bounded (`models.MAX_PROSE_LENGTH`).

Both travel in every `usecase.upserted`, and the event channel carries an event only up to its
size. Validation only: PostgreSQL's `text` stores either way, so no row changes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0016_payload_visibility"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usecase",
            name="description",
            field=models.TextField(blank=True, max_length=20000),
        ),
        migrations.AlterField(
            model_name="usecase",
            name="processing_notes",
            field=models.TextField(blank=True, max_length=20000),
        ),
    ]
