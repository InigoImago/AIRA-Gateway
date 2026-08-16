"""A pipeline does not decide which model a use case is entered at (owner's decision).

`0002` added `start_model` so the question catalogue would have somewhere to begin. The owner's
objection is the one that matters: a use case releases **several** models on purpose, and naming
one on the pipeline reads as *this is the model this use case uses* — it narrows, in the reader's
mind, a decision the release deliberately left open.

The catalogue does need a model to enter at. It now asks for one **when a run is started**, from
the models already released to that use case (`FRD-504` §5.8). That is where the question belongs:
it is a property of the run, not of the pipeline, and two runs of one use case may legitimately
enter at two different models — which is exactly the comparison a person evaluating models wants
and the pipeline field made awkward.

Nothing is lost by dropping the column. It was written by the seed and by the pipeline editor, and
every value it held is recoverable: the models a use case may be entered at *are* the models
released to it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0002_pipelineconfig_start_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pipelineconfig",
            name="start_model",
        ),
    ]
