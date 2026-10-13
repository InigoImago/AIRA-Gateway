"""An event the broker will never accept is parked with its reason rather than retried for ever,
and the relay's one question — the oldest pending events — gets an index of its own."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("outbox", "0003_outboxevent_traceparent"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxevent",
            name="parked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="parked_reason",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(
                condition=models.Q(("parked_at__isnull", True), ("published_at__isnull", True)),
                fields=["created_at", "id"],
                name="outbox_pending",
            ),
        ),
    ]
