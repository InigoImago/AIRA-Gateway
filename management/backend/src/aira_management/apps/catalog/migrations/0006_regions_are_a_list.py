from typing import Any

from django.db import migrations


def _forwards(apps: Any, schema_editor: Any) -> None:
    """`{"region": "x"}` becomes `{"regions": ["x"]}` (`FRD-609`).

    A model may now live in several places and be tried in order, so the field that held one is a
    list. Converted rather than read leniently forever: two spellings in a stored column are two
    things a reader has to keep agreeing about, and the readers are in three planes.

    The lenient readers stay anyway — `ModelDeclaration.regions`, `_declared_regions`,
    `readRegions` — because a redelivered Kafka event can carry the old shape after a rollback, and
    because a row written by a hand or an older console is not this migration's to prevent. What
    they must not do is *disagree*, which is what `test_the_two_readers_of_a_region_list_agree`
    pins.
    """
    Model = apps.get_model("catalog", "Model")
    for model in Model.objects.exclude(addressing=None).iterator():
        block = model.addressing
        if not isinstance(block, dict) or "regions" in block:
            continue
        region = block.pop("region", None)
        if isinstance(region, str) and region.strip():
            block["regions"] = [region.strip()]
        model.addressing = block
        model.save(update_fields=["addressing"])


def _backwards(apps: Any, schema_editor: Any) -> None:
    """The **first** region back into the singular field, and the rest dropped.

    Lossy, and stated rather than hidden: the old shape holds one region and a rollback cannot
    invent a place to put the others. The first is the right one to keep — it is where an ordinary
    request went before the rollback and where it will go after.
    """
    Model = apps.get_model("catalog", "Model")
    for model in Model.objects.exclude(addressing=None).iterator():
        block = model.addressing
        if not isinstance(block, dict):
            continue
        regions = block.pop("regions", None)
        if isinstance(regions, list) and regions:
            block["region"] = str(regions[0])
        model.addressing = block
        model.save(update_fields=["addressing"])


class Migration(migrations.Migration):
    """A model's region becomes a list of regions, tried in order (`FRD-609`)."""

    dependencies = [("catalog", "0005_thinking_levels_are_words")]

    operations = [migrations.RunPython(_forwards, _backwards)]
