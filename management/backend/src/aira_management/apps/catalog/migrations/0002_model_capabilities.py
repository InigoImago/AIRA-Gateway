"""Model capability metadata (FRD-114).

Every field is nullable or defaulted, so existing rows stay valid and an installation that has
only ever set prices keeps working — those models are simply *undeclared*, which the gateway reads
as the baseline capabilities and nothing more (FR-7).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="model",
            name="addressing",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="attachments",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="capabilities",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="model",
            name="default_max_output_tokens",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="deprecated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="model",
            name="embedding",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="hosting",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="model",
            name="max_output_tokens",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="numeric_id",
            field=models.PositiveIntegerField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="model",
            name="platform",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="model",
            name="publisher",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="model",
            name="thinking",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="model",
            name="underlying_model",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
