"""The migration `FRD-308` never wrote.

The field's declaration was changed when the release replaced `allow_check`, and no migration was
generated for it — so `makemigrations --check` had been reporting a pending `AlterField` ever
since, and the recorded schema and the models disagreed. Nothing behaved wrongly (the alteration
does not change how the column stores anything), which is exactly why it survived: the drift lands
on whoever runs `makemigrations` next, where it is swept silently into their unrelated change.

Written by hand from the generated output only to wrap the help text; the operation is the
generator's. `test_migrations_match_the_models` now fails on the next one.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_cache_prices"),
        ("usecases", "0010_release_models_from_allow_check"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usecase",
            name="allowed_models",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "The models this use case may call (FRD-308). Empty means none: a model is "
                    "released for a use case, never assumed. Only approved models can be released."
                ),
                related_name="use_cases",
                to="catalog.model",
            ),
        ),
    ]
