"""The trace context of the request that caused an event (`FRD-615`).

Blank on every existing row, which is what it means: those events were published before the
context was captured, and there is nothing to reconstruct it from.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("outbox", "0002_alter_outboxevent_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxevent",
            name="traceparent",
            field=models.CharField(blank=True, default="", max_length=128),
            preserve_default=False,
        ),
    ]
