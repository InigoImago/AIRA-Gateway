"""One flat catalogue: the battery goes away (`FRD-504`).

Questions were grouped into named batteries. The owner's answer was that there is nothing to
group — there is a catalogue, and every model is asked all of it. Grouping also quietly cost the
property that makes the catalogue a standard: with several batteries, "how does this model do" has
as many answers as there are groups, and none compares to a model asked a different group.

**Nothing is deleted.** Positions were unique per battery, so eight batteries each had a question
at position 1; they are renumbered into one sequence, ordered by battery name and then position, so
the catalogue reads in the order it did before. Existing runs and the verdicts already given
against them survive: a run is evidence about what a model did on a day, and reorganising the
catalogue is not a reason to lose it.
"""

from typing import Any

from django.db import migrations, models


def renumber_into_one_catalogue(apps: Any, schema_editor: Any) -> None:
    TestCase = apps.get_model("smoketests", "TestCase")
    ordered = TestCase.objects.order_by("battery__name", "position", "id")
    # Retired questions keep a position too, but are excluded from the uniqueness constraint. They
    # are numbered after the live ones so a retired row can never collide with a declared one.
    live = [case for case in ordered if not case.retired]
    retired = [case for case in ordered if case.retired]
    for index, case in enumerate(live + retired, start=1):
        case.position = index
        case.save(update_fields=["position"])


class Migration(migrations.Migration):
    dependencies = [("smoketests", "0002_retire_superseded_questions")]

    operations = [
        migrations.RemoveConstraint(
            model_name="testcase",
            name="unique_position_per_battery",
        ),
        migrations.RunPython(renumber_into_one_catalogue, migrations.RunPython.noop),
        migrations.RemoveField(model_name="testcase", name="battery"),
        migrations.RemoveField(model_name="testrun", name="battery"),
        migrations.AddConstraint(
            model_name="testcase",
            constraint=models.UniqueConstraint(
                condition=models.Q(("retired", False)),
                fields=("position",),
                name="unique_position_in_catalogue",
            ),
        ),
        migrations.DeleteModel(name="TestBattery"),
    ]
