"""Remove budgets that name one person (2026-08-14).

The ``member`` scope is gone on the owner's decision: singling somebody out is not a governance
decision this product wants to make easy, and what remains — a shared pot, or the same allowance
for everybody — says what an administrator actually needs.

**The rows go with it.** Leaving them would be worse than either keeping the feature or removing
it: the gateway's `Scope.applying` no longer resolves that scope, so such a budget would be
enforced by nothing while still sitting in the table — a rule that exists, cannot be seen in the
console, and does nothing. This project has a name for that shape and has paid for it repeatedly.

Deleted rather than converted to `each_member`. A cap somebody set for one person is not the same
decision as a cap for everybody, and quietly widening it to the whole use case would be inventing a
governance decision nobody made.

Irreversible on purpose: `reverse_code` cannot restore what a delete removed, and pretending
otherwise would make `migrate` report a rollback that did not happen.
"""

from typing import Any

from django.db import migrations, models


def drop_member_budgets(apps: Any, schema_editor: Any) -> None:
    budget = apps.get_model("budgets", "Budget")
    removed = budget.objects.filter(scope="member").delete()[0]
    if removed:
        print(f"  removed {removed} budget(s) scoped to one named person")


class Migration(migrations.Migration):
    dependencies = [("budgets", "0003_alter_budget_scope")]

    operations = [
        migrations.RunPython(drop_member_budgets, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="budget",
            name="scope",
            field=models.CharField(
                choices=[("use_case", "Use case"), ("each_member", "Each member")], max_length=16
            ),
        ),
    ]
