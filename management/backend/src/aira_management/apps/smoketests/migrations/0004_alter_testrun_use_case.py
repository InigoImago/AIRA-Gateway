"""A run names the use case whose pipeline it exercised, and it is no longer optional (`ADR-0020`).

The column was `blank=True` because a run was about a *model* and the use case was where its cost
happened to land — one seeded slug, filled in by the console. A run is now **about** the use case,
so a blank one describes nothing.

No data migration, and that is deliberate. Every existing row already carries the seeded slug —
there was only one place to book a run — so the field is already populated everywhere. If some
installation somehow holds a run with a blank use case, it is a run whose subject was never
recorded, and inventing one here would attribute somebody's evidence to a use case that did not
produce it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("smoketests", "0003_one_flat_catalogue"),
    ]

    operations = [
        migrations.AlterField(
            model_name="testrun",
            name="use_case",
            field=models.CharField(max_length=64),
        ),
    ]
