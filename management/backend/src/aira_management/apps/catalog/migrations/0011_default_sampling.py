"""A model's sampling defaults, applied where a caller sets none.

Nothing is announced: every row takes `null`, the model's own defaults, which is also what the
gateway assumes for a model event that does not name the field, so both planes already agree.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0010_fixed_sampling"),
    ]

    operations = [
        migrations.AddField(
            model_name="model",
            name="default_sampling",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
