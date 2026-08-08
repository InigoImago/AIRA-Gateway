"""An API key may state an end date.

NULL means never, which is what every key issued before this migration carries.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("apikeys", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="apikey",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
