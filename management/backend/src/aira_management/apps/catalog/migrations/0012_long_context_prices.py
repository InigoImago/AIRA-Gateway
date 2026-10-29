from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """What a request costs whose prompt is longer than a stated length (`FRD-408`).

    Nothing is announced: every row takes `null`, one set of prices at every length, which is also
    what the gateway reads from a model event that does not name the fields.
    """

    dependencies = [
        ("catalog", "0011_default_sampling"),
    ]

    operations = [
        migrations.AddField(
            model_name="model",
            name="long_context_threshold",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="The longest prompt, in tokens, the ordinary prices apply to",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="model",
            name="long_context_input_price_per_million",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="Price per 1,000,000 input tokens of a request whose prompt is longer",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="model",
            name="long_context_output_price_per_million",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="Price per 1,000,000 output tokens of a request whose prompt is longer",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="model",
            name="long_context_cached_input_price_per_million",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text=(
                    "Price per 1,000,000 cached input tokens of a request whose prompt is longer"
                ),
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="model",
            name="long_context_cache_write_price_per_million",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text=(
                    "Price per 1,000,000 tokens written into the cache by a request whose prompt "
                    "is longer"
                ),
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
    ]
