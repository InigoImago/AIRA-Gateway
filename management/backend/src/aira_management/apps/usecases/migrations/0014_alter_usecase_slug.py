"""A slug validator anchored at the end of the string, not before a trailing newline (`FRD-613`).

No data changes: `RegexValidator` runs in Python, so this migration exists only to keep the
recorded model state in step with the field. The correction it records is one character — `$`
matches before a trailing newline in Python, so `"kundenservice\n"` satisfied a validator whose
whole purpose is that this string carries nothing but `[a-z0-9-]`, and this string is a **primary
key on the other plane**.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0013_soft_delete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usecase",
            name="slug",
            field=models.CharField(
                max_length=64,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Use lowercase letters, digits, and hyphens only.",
                        regex="^[a-z0-9-]+\\Z",
                    )
                ],
            ),
        ),
    ]
