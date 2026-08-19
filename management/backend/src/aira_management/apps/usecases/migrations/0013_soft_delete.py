from django.db import migrations, models


class Migration(migrations.Migration):
    """A deleted use case becomes a tombstone rather than a hole (`FRD-607`).

    Nothing is backfilled: every row that exists has never been deleted, and a `deleted_at` of
    NULL says exactly that. The rows this feature exists to preserve are the ones that were
    *already* destroyed before it — and those cannot be recovered, which is the argument for
    shipping it rather than a note about it.
    """

    dependencies = [("usecases", "0012_include_reasoning")]

    operations = [
        migrations.AddField(
            model_name="usecase",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="usecase",
            name="deleted_by",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
