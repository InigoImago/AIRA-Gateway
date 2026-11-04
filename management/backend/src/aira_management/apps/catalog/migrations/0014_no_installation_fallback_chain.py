"""The installation keeps no fallback chain (`ADR-0052`): a use case keeps its own.

After `pipelines/0005`, which writes every chain set here into the use cases that followed it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0013_fallback_chains"),
        ("pipelines", "0005_fallback_belongs_to_the_use_case"),
    ]

    operations = [
        migrations.DeleteModel(name="FallbackChain"),
    ]
