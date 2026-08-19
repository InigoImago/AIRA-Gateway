from typing import Any

from django.db import migrations

#: The four level members the vocabulary used to carry, before `ADR-0021` split it into three
#: control settings the gateway owns and any number of vendor words.
_LEVELS = ("minimal", "low", "medium", "high")
_CONTROL = ("disabled", "auto", "limited")


def _forwards(apps: Any, schema_editor: Any) -> None:
    """Turn `{modes: [... levels ...], levels: {level: tokens}}` into words.

    The numbers are **dropped, not converted**, and that is the migration rather than a loss. They
    were typed by whoever catalogued the model, out of nothing — no vendor publishes what `medium`
    costs — and they were then sent as a ceiling on the model's reasoning. Keeping them would keep
    exactly the failure this change is about: a hand-typed `medium = 2000` truncating an agentic
    run that needed twenty thousand thinking tokens, with nothing in the answer to say why.

    Which words a model really takes is a question for the model, and the console asks it with one
    capped request. So this moves each declared level across as a word and leaves the checking to
    the place that can actually answer.
    """
    Model = apps.get_model("catalog", "Model")
    for model in Model.objects.exclude(thinking=None).iterator():
        block = model.thinking
        if not isinstance(block, dict):
            continue
        declared = block.get("modes")
        declared = declared if isinstance(declared, list) else []
        words = [mode for mode in declared if isinstance(mode, str) and mode in _LEVELS]
        # A pre-existing `levels` table may name a level that `modes` did not offer. That was dead
        # configuration and it stays dead: only what the model was actually offering moves.
        block["modes"] = [mode for mode in declared if mode in _CONTROL]
        if words:
            block["levels"] = words
        else:
            block.pop("levels", None)
        model.thinking = block
        model.save(update_fields=["thinking"])


def _backwards(apps: Any, schema_editor: Any) -> None:
    """Words back into `modes`, with no table — there is nothing to put back into one.

    Deliberately not a reconstruction: the numbers this migration dropped cannot be recovered and
    must not be invented a second time. A catalog rolled back declares its levels and no budgets,
    which is the state the old code treats as "resolve to the ceiling".
    """
    Model = apps.get_model("catalog", "Model")
    for model in Model.objects.exclude(thinking=None).iterator():
        block = model.thinking
        if not isinstance(block, dict):
            continue
        words = block.pop("levels", None)
        declared = block.get("modes")
        declared = declared if isinstance(declared, list) else []
        if isinstance(words, list):
            block["modes"] = declared + [
                w for w in words if isinstance(w, str) and w not in declared
            ]
        model.thinking = block
        model.save(update_fields=["thinking"])


class Migration(migrations.Migration):
    """Thinking levels stop being a token table and become the vendor's own words (`ADR-0021`)."""

    dependencies = [("catalog", "0004_cache_prices")]

    operations = [migrations.RunPython(_forwards, _backwards)]
