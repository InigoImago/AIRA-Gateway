"""Two use case settings for mail (`FRD-409`): at which shares of a budget a mail goes out, `[100]`
by default, and whether the administrators hear of the use case's anomaly findings, off by default.

Both defaults are what the gateway reads for an event without the fields, so no use case needs
announcing again: each one reads on the gateway exactly as it is stored here.
"""

from django.db import migrations, models

import aira_management.apps.usecases.models


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0018_payload_visibility_says_what_it_withholds"),
    ]

    operations = [
        migrations.AddField(
            model_name="usecase",
            name="budget_mail_thresholds",
            field=models.JSONField(
                blank=True,
                default=aira_management.apps.usecases.models.default_budget_mail_thresholds,
                help_text=(
                    "At which shares of a budget a mail goes out, in percent (FRD-409): to the "
                    "person about their own allowance, to the administrators about the use case's "
                    "shared budget. Up to five, from 1 to 100; [100] by default, the moment a "
                    "budget is used up; empty for none."
                ),
            ),
        ),
        migrations.AddField(
            model_name="usecase",
            name="mail_admins_on_anomalies",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Mail this use case's administrators when one of its anomaly rules finds "
                    "something (FRD-409). The mail names the rule and what it measured, never a "
                    "person, key or address."
                ),
            ),
        ),
    ]
