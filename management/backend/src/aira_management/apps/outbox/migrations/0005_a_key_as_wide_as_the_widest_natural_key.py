"""An event's record key holds its entity's whole natural key, the widest a group grant's: a slug,
`|` and a group path, up to 320 characters (`models.KEY_MAX_LENGTH`).

Widening a `varchar` changes no stored row, and PostgreSQL does it without rewriting the table.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("outbox", "0004_parked_events_and_a_pending_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="outboxevent",
            name="key",
            field=models.CharField(blank=True, max_length=512),
        ),
    ]
