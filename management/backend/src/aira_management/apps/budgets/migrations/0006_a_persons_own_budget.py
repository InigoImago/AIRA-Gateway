"""A budget may name one person again, as their own budget (`FRD-407`, `ADR-0044`).

Not the scope `0004_drop_member_scope` removed: that one bound a person *on top of* every other
budget and could only lower what they had. This one takes the place of the per-person budget of
its period for the person it names — the hierarchy the gateway applies — so it raises an allowance
as readily as it lowers one.

**Names are cleared from every other row first**, before the constraint that refuses them: the
serializer has dropped a subject sent with any scope since `0004`, so none is expected, and a row
the constraint would refuse must not be what stops a deployment. Clearing one changes nothing the
gateway does — it reads a subject only on the scope added here — so nothing is announced.
"""

from typing import Any

from django.db import migrations, models


def clear_names_on_shared_rows(apps: Any, schema_editor: Any) -> None:
    budget = apps.get_model("budgets", "Budget")
    cleared = budget.objects.exclude(scope="member").exclude(subject="").update(subject="")
    if cleared:
        print(f"  cleared the name on {cleared} budget(s) that name nobody by scope")


class Migration(migrations.Migration):
    dependencies = [("budgets", "0005_installation_budget")]

    operations = [
        # Reversible as a no-op: a cleared name was one nothing read.
        migrations.RunPython(clear_names_on_shared_rows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="budget",
            name="scope",
            field=models.CharField(
                choices=[
                    ("use_case", "Use case"),
                    ("each_member", "Each member"),
                    ("member", "One person"),
                    ("installation", "Installation"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="budget",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("scope", "member"), models.Q(("subject", ""), _negated=True)),
                    models.Q(models.Q(("scope", "member"), _negated=True), ("subject", "")),
                    _connector="OR",
                ),
                name="ck_budget_names_only_one_person",
            ),
        ),
    ]
