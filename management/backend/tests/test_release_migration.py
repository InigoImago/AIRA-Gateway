"""The `allow_check` step becomes a release, and nothing else does (`FRD-308`).

Two properties, and the second is the one that would be tempting to get wrong.

A use case that had the step made a **decision**: somebody opened the builder, added it and typed
model names. Dropping that would take a choice away silently, so it is carried over.

A use case that never had the step could call every approved model. Writing the whole approved
catalog into it would keep it working — and would show, in a console built to record who released
what, a release nobody made. `FRD-122`'s rule about the audit row is the same one: an unverifiable
claim is not evidence. Those use cases start empty and stop serving, which is what the owner chose
knowing the consequence.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.usecases.models import UseCase

pytestmark = pytest.mark.django_db

#: The migration module, by import — its name starts with a digit, so it cannot be a plain import.
migration = import_module(
    "aira_management.apps.usecases.migrations.0010_release_models_from_allow_check"
)


class _Apps:
    """The historical-model registry a data migration is handed. The real models are close enough
    here — none of the fields this touches has changed since."""

    _by_name = {
        ("usecases", "UseCase"): UseCase,
        ("catalog", "Model"): Model,
        ("pipelines", "PipelineConfig"): PipelineConfig,
    }

    def get_model(self, app: str, name: str):  # noqa: ANN201
        return self._by_name[(app, name)]


def _pipeline(usecase: UseCase, steps: list[dict]) -> PipelineConfig:
    return PipelineConfig.objects.create(use_case=usecase, steps=steps, fallback_models=[])


def test_an_allow_check_list_becomes_the_release() -> None:
    Model.objects.create(name="chosen-1", approved=True)
    Model.objects.create(name="chosen-2", approved=True)
    Model.objects.create(name="never-mentioned", approved=True)
    usecase = UseCase.objects.create(slug="uc", name="UC")
    _pipeline(usecase, [{"type": "allow_check", "config": {"models": ["chosen-1", "chosen-2"]}}])

    migration._release_what_allow_check_permitted(_Apps(), None)

    assert sorted(m.name for m in usecase.allowed_models.all()) == ["chosen-1", "chosen-2"]


def test_a_name_the_catalog_never_had_is_not_carried_over() -> None:
    """The step could name a model nobody catalogued — it was refused at dispatch anyway
    (`FRD-307`), and carrying it into a release would put a name in the console that can never
    work."""
    Model.objects.create(name="real-1", approved=True)
    usecase = UseCase.objects.create(slug="uc", name="UC")
    _pipeline(usecase, [{"type": "allow_check", "config": {"models": ["real-1", "imaginary-1"]}}])

    migration._release_what_allow_check_permitted(_Apps(), None)

    assert [m.name for m in usecase.allowed_models.all()] == ["real-1"]


def test_a_use_case_that_never_had_the_step_is_left_empty() -> None:
    """The property that is tempting to break. Filling these in from the approved catalog would
    keep every use case running and would record a decision nobody made."""
    Model.objects.create(name="approved-1", approved=True)
    usecase = UseCase.objects.create(slug="uc", name="UC")
    _pipeline(usecase, [{"type": "injection_filter", "config": {"mode": "heuristic"}}])

    migration._release_what_allow_check_permitted(_Apps(), None)

    assert list(usecase.allowed_models.all()) == []


def test_the_step_is_removed_from_the_stored_pipeline() -> None:
    """Left behind it would be a second answer to "which models may this use case use" — and the
    console no longer shows it, so the one that disagreed would be the invisible one."""
    usecase = UseCase.objects.create(slug="uc", name="UC")
    config = _pipeline(
        usecase,
        [
            {"type": "allow_check", "config": {"models": ["a"]}},
            {"type": "injection_filter", "config": {"mode": "heuristic"}},
        ],
    )

    migration._drop_the_step(_Apps(), None)

    config.refresh_from_db()
    assert [step["type"] for step in config.steps] == ["injection_filter"]


def test_running_it_twice_changes_nothing_the_second_time() -> None:
    """A migration that is re-run — a replayed deploy, a restored dump — must not accumulate."""
    Model.objects.create(name="chosen-1", approved=True)
    usecase = UseCase.objects.create(slug="uc", name="UC")
    _pipeline(usecase, [{"type": "allow_check", "config": {"models": ["chosen-1"]}}])

    migration._release_what_allow_check_permitted(_Apps(), None)
    migration._release_what_allow_check_permitted(_Apps(), None)

    assert usecase.allowed_models.count() == 1
