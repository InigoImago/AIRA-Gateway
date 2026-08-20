"""The gateway holds a pipeline to the bounds Management applies when it is written (2026-08-15).

`aira_common.patterns` states the rule and gives the reason:

> **Both planes ask, because only one of them used to.** Management refused such a pattern at
> authoring time and the gateway compiled whatever reached its read-model […] The protection sat at
> one end of a link and the other end trusted it, which is the shape of three of the four findings
> in `ADR-0018`.

That was acted on for the regex bound and for the step count, and not for the rest. `instruction`,
`notice` and the category list arrived here unbounded, and there are three ways in that need no
privileges Management would check: a row written straight into the read-model, a publish onto a
broker (`KafkaSecurity` documents that the bus is a trust boundary), and
`POST /v1beta/pipeline:dryRun`, whose `pipeline` field is an unvalidated object by design.

What each costs, unbounded: an `instruction` is a system prompt sent on **every** request of the
use case, so its length is a bill; a `notice` is put in front of somebody else's answer, so its
length is their screen; a category list is pasted whole into the router's prompt.

**Truncated with a log line rather than dropped** — the same treatment the pattern bounds beside
them already give. A step running on a shortened instruction is degraded; a use case whose filter
vanished is unprotected, and only one of the two announces itself.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.pipeline.config import (
    MAX_CATEGORIES,
    MAX_FALLBACK_MODELS,
    MAX_MODEL_LENGTH,
    MAX_STEPS,
    MAX_TEXT_LENGTH,
    Pipeline,
    StepType,
)

LONG = "x" * (MAX_TEXT_LENGTH * 3)


def _parsed(**config: Any) -> dict[str, Any]:
    pipeline = Pipeline.from_dict(
        {"steps": [{"type": "injection_filter", "config": config}], "fallback_models": []}
    )
    return dict(pipeline.steps[0].config)


def test_an_instruction_is_held_to_the_length_management_allows() -> None:
    """It is a system prompt on every request of the use case. Its length is a bill."""
    assert len(_parsed(instruction=LONG)["instruction"]) == MAX_TEXT_LENGTH


def test_a_notice_is_bounded_because_it_goes_in_front_of_somebody_elses_answer() -> None:
    parsed = Pipeline.from_dict({"steps": [{"type": "model_route", "config": {"notice": LONG}}]})
    assert len(parsed.steps[0].config["notice"]) == MAX_TEXT_LENGTH


def test_a_short_instruction_is_left_exactly_as_written() -> None:
    """Nothing may be rewritten that did not have to be: an operator's text is theirs."""
    instruction = "Antworte nur mit SAFE oder INJECTION."
    assert _parsed(instruction=instruction)["instruction"] == instruction


def test_a_category_list_is_bounded_and_each_field_in_it_too() -> None:
    """The list is pasted whole into the router's prompt, and every field of every entry with it."""
    categories = [
        {"name": LONG, "description": LONG, "model": LONG} for _ in range(MAX_CATEGORIES * 2)
    ]
    parsed = Pipeline.from_dict(
        {"steps": [{"type": "model_route", "config": {"categories": categories}}]}
    )

    kept = parsed.steps[0].config["categories"]
    assert len(kept) == MAX_CATEGORIES
    for field in ("name", "description", "model"):
        assert len(kept[0][field]) == MAX_TEXT_LENGTH


def test_a_malformed_category_is_carried_through_rather_than_dropped() -> None:
    """Forward compatibility, exactly as `from_dict` treats an unknown step: this build does not
    know what it is, and the step that reads it can decide. Dropping it here would make a newer
    Management's configuration silently smaller."""
    parsed = Pipeline.from_dict(
        {"steps": [{"type": "model_route", "config": {"categories": ["not-an-object"]}}]}
    )
    assert parsed.steps[0].config["categories"] == ["not-an-object"]


def test_the_bounds_that_were_already_asked_still_are() -> None:
    """The two that were carried across from the start, kept beside the four that were not."""
    parsed = Pipeline.from_dict(
        {
            "steps": [{"type": "injection_filter", "config": {}} for _ in range(MAX_STEPS * 2)],
            "fallback_models": [f"m{index}" for index in range(MAX_FALLBACK_MODELS * 2)],
        }
    )

    assert len(parsed.steps) == MAX_STEPS
    assert len(parsed.fallback_models) == MAX_FALLBACK_MODELS


def test_a_fallback_model_name_is_bounded_as_well_as_counted() -> None:
    """`MAX_MODEL_LENGTH` was declared in this module and read by nothing.

    The chain bounded how *many* models it may name and not how long each name may be, and a name
    does more than fail a lookup: an unresolvable candidate is recorded on the audit row as
    `{"step": "dispatch", "action": "skipped", "to": <name>}` in a `json` column, and named back to
    the caller inside the `NoCapableModel` message. Three ways in reach this parser without passing
    Management's serializer — a row written straight into the read-model, a publish onto an
    unauthenticated broker, and `pipeline:dryRun`, whose `pipeline` field is unvalidated by design.
    """
    parsed = Pipeline.from_dict({"fallback_models": ["m" * (MAX_MODEL_LENGTH * 10)]})

    assert parsed.fallback_models == ("m" * MAX_MODEL_LENGTH,)


def test_a_step_type_this_build_does_not_know_is_still_dropped() -> None:
    """Unchanged: an unknown *step* is skipped so an older gateway tolerates newer config, while an
    over-long *field* of a known step is clipped so the step keeps working."""
    parsed = Pipeline.from_dict(
        {
            "steps": [
                {"type": "something_new", "config": {"instruction": LONG}},
                {"type": "injection_filter", "config": {}},
            ]
        }
    )

    assert [step.type for step in parsed.steps] == [StepType.INJECTION_FILTER]
