"""Seed contribution: the question catalogue and IT Security's evaluation use case (`FRD-504`).

The questions are data, in `test_catalogue_data`; `seed_test_catalogue` reads `QUESTIONS` from this
module's namespace at call time.
"""

from __future__ import annotations

import os
from typing import Any

from django.db import transaction

from aira_management.apps.catalog.models import Model
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.seed.contributions.test_catalogue_data import QUESTIONS
from aira_management.apps.seed.registry import SeedResult, register
from aira_management.apps.smoketests.models import DEMO_MODEL_TEST_USE_CASE, TestCase
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views import _snapshot
from aira_management.apps.usecases.views.payloads import _pipeline_payload

#: IT Security's model-evaluation use case (`ADR-0020`) — an ordinary use case, seeded so a fresh
#: installation has somewhere to demonstrate a model test from.
EVALUATION_USE_CASE: dict[str, Any] = {
    "name": "Model evaluation",
    "description": (
        "IT Security's use case for putting the question catalogue to a model. Its "
        "pipeline starts at the model under evaluation and does nothing else, so the "
        "answers are the model's own — which is what makes it a *model* test rather "
        "than a test of somebody's filter."
    ),
    "processing_notes": (
        "No personal data: the catalogue is a fixed list of questions. Gateway payload "
        "storage is off, because Management already keeps every prompt and answer with "
        "its verdict — storing them twice would put the same content under two "
        "different retention clocks."
    ),
    "store_payloads": False,
    "retention_days": 1,
}


def _evaluation_use_case() -> list[str]:
    return [DEMO_MODEL_TEST_USE_CASE]


# After the showcase, so a reader who has just seen the use cases finds the catalogue next.
@register(name="test_catalogue", order=60, use_cases=_evaluation_use_case)
def seed_test_catalogue(fresh: bool) -> SeedResult:
    """Idempotent, and it **only fills positions nobody has used**, live or retired.

    The catalogue is seeded and then owned by IT Security in the console (`test_catalogue_data`),
    and nothing records which rows the seed wrote, so a seed that wrote to an existing position
    would undo the owner's work: reword a question they had reworded, bring back one they had
    retired, or retire one they had added past the end. A position is the question's identity — a
    rename keyed on the topic would put a second question beside the old one — so a position in
    use, by anybody, is left as it is.
    """
    added = 0
    with transaction.atomic():
        use_case, _created = UseCase.objects.update_or_create(
            slug=DEMO_MODEL_TEST_USE_CASE, defaults=EVALUATION_USE_CASE
        )
        _point_it_at_a_model(use_case)
        # The event, not just the row: the gateway learns configuration over Kafka (`FRD-204`)
        # and refuses every request for a use case it has not heard of.
        events.emit("usecase.upserted", _snapshot(use_case))
        used = set(TestCase.objects.values_list("position", flat=True))
        for position, (topic, prompt, expectation) in enumerate(QUESTIONS, start=1):
            if position in used:
                continue
            TestCase.objects.create(
                position=position, topic=topic, prompt=prompt, expectation=expectation
            )
            added += 1
    return {"use_case": DEMO_MODEL_TEST_USE_CASE, "questions": added}


def _point_it_at_a_model(use_case: UseCase) -> None:
    """Release a model to the evaluation use case, so a run has somewhere to enter (`ADR-0020`).

    Configuration done by the seed, as an administrator would: a run never writes a release itself
    (`FRD-308`). Silent when nothing is approved — inventing a release for a model that does not
    exist would make a use case that refuses every request; the console then says it has no model.
    """
    chosen = (
        Model.objects.filter(
            name=os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", ""), approved=True
        ).first()
        or Model.objects.filter(approved=True).order_by("name").first()
    )
    if chosen is None:
        return

    use_case.allowed_models.add(chosen)
    config, _made = PipelineConfig.objects.update_or_create(
        use_case=use_case,
        # **No steps**: a model test wants the model's own answer, not a test of a filter.
        defaults={"steps": [], "fallback_models": []},
    )
    events.emit("pipeline.upserted", _pipeline_payload(use_case.slug, config))
