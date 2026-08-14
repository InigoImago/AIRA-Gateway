"""Remove rate limits that name one person (2026-08-14).

See `budgets/migrations/0004` — the same decision, the same reasoning, and the same reason the rows
are deleted rather than left behind or widened.
"""

from typing import Any

from django.db import migrations, models


def drop_member_limits(apps: Any, schema_editor: Any) -> None:
    rate_limit = apps.get_model("ratelimits", "RateLimit")
    removed = rate_limit.objects.filter(scope="member").delete()[0]
    if removed:
        print(f"  removed {removed} rate limit(s) scoped to one named person")


class Migration(migrations.Migration):
    dependencies = [("ratelimits", "0002_alter_ratelimit_scope")]

    operations = [
        migrations.RunPython(drop_member_limits, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="ratelimit",
            name="scope",
            field=models.CharField(
                choices=[("use_case", "Use case"), ("each_member", "Each member")], max_length=16
            ),
        ),
    ]
