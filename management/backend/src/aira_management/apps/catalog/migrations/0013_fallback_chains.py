"""The installation's fallback chain per kind of request (`FRD-310`).

No row is an empty chain, which is the default: a model that cannot answer is the caller's error,
as it was before this table existed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0012_long_context_prices"),
    ]

    operations = [
        migrations.CreateModel(
            name="FallbackChain",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("generate", "generate"),
                            ("embed", "embed"),
                            ("rerank", "rerank"),
                        ],
                        max_length=16,
                        unique=True,
                    ),
                ),
                ("chain", models.JSONField(default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["kind"]},
        ),
    ]
