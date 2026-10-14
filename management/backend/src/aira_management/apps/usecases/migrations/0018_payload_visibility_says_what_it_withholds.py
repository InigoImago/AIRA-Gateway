"""The content setting's label and help text say what it withholds (`FRD-622`, `FRD-603`).

`oversight` leaves content to every role that may read any content (`payload.read_any`), which an
installation may give to roles of its own, and it withholds other people's figures per person
too. Text only: no column or value changes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("usecases", "0017_prose_is_bounded"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usecase",
            name="payload_visibility",
            field=models.CharField(
                choices=[
                    ("members", "Every member of the use case"),
                    ("own", "Administrators, and each person's own requests"),
                    ("oversight", "Oversight roles only"),
                ],
                default="members",
                help_text=(
                    "Who inside this use case may read stored prompts and responses, and whose "
                    "figures per person they see (FRD-622, FRD-603). 'members': everybody in the "
                    "use case, everything. 'own': administrators of the use case read all of it, "
                    "everybody else only their own requests and figures. 'oversight': nobody in "
                    "the use case, not even its administrators — only roles that may read any "
                    "content (payload.read_any: Global Administrator and IT Security, and any "
                    "role given it); each reader sees their own figures. The use case's totals "
                    "are unaffected."
                ),
                max_length=16,
            ),
        ),
    ]
