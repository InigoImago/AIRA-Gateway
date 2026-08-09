"""Only an approved model may be used (`FRD-307`).

Everything already in the catalog stays usable. A migration that switched every existing model off
would be a governance improvement delivered as an outage — approval is a decision about *new*
models, and nobody made a decision about the old ones by installing an update.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_model_capabilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="model",
            name="approved",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Only a Global Administrator may approve a model, and only an approved model "
                    "may be used by a use case (FRD-307). Default off: a model appearing on an "
                    "upstream is not the same event as somebody deciding it may be used here."
                ),
            ),
        ),
        migrations.RunSQL(
            "UPDATE catalog_model SET approved = true",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
