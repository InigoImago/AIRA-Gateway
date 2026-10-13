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

import pytest

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


def test_a_category_that_is_not_an_object_is_left_out_and_the_rest_kept() -> None:
    """The router reads a category's name and model off an object; a string there made every
    request of the use case a 500 rather than reaching a step that could decide anything. Left out
    with a log line — and only it: the well-formed categories beside it still route."""
    parsed = Pipeline.from_dict(
        {
            "steps": [
                {
                    "type": "model_route",
                    "config": {"categories": ["not-an-object", {"name": "code", "model": "m"}]},
                }
            ]
        }
    )
    assert parsed.steps[0].config["categories"] == [{"name": "code", "model": "m"}]


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


# == a stored row of the wrong shape degrades a step, not the use case (`G4-10`) ==================
#
# The same parser feeds the read-model, which a direct write or a forged event can reach
# (`ADR-0018`). A stored `steps: null` made every request of the use case answer 500.


@pytest.mark.parametrize(
    "document",
    [
        None,
        "steps",
        {"steps": None},
        {"steps": "injection_filter"},
        {"steps": 5},
        {"fallback_models": 5},
        {"steps": [None, 7, "x", {"type": ["injection_filter"]}]},
    ],
)
def test_a_pipeline_of_the_wrong_shape_parses_to_what_can_be_used(document: Any) -> None:
    assert Pipeline.from_dict(document) == Pipeline()


def test_each_field_a_step_cannot_use_is_left_out_and_the_step_kept() -> None:
    parsed = Pipeline.from_dict(
        {
            "steps": [
                {
                    "type": "injection_filter",
                    "config": {
                        "patterns": ["ok", 1, None],
                        "instruction": 5,
                        "model": ["guard"],
                        "fallback_models": [None, 7, "second"],
                    },
                },
                {"type": "model_route", "config": {"categories": "oops", "default_model": 3}},
            ],
            "fallback_models": ["a", 5, None],
        }
    )

    assert parsed.steps[0].config == {"patterns": ["ok"], "fallback_models": ["second"]}
    assert parsed.steps[1].config == {}
    assert parsed.fallback_models == ("a",)


def test_a_word_outside_a_closed_vocabulary_is_read_the_way_that_refuses_or_reads_more() -> None:
    """`M2-3`: the parser answers each closed vocabulary itself, so no step meets a word it does not
    know — an `action` of `Block` is `block`, never `flag`; a `scope` of `system-user` reads every
    message; a policy nobody can name blocks; and a `mode` of `LLM` is the heuristic."""
    parsed = Pipeline.from_dict(
        {
            "steps": [
                {
                    "type": "injection_filter",
                    "config": {
                        "action": "Block",
                        "scope": "system-user",
                        "mode": "LLM",
                        "on_undetermined": "Allow",
                    },
                },
                {"type": "pii_filter", "config": {"on_failure": "ALLOW"}},
            ]
        }
    )

    assert parsed.steps[0].config == {
        "action": "block",
        "scope": "system_user",
        "mode": "heuristic",
        "on_undetermined": "block",
    }
    assert parsed.steps[1].config == {"on_failure": "block"}


def test_a_known_word_and_an_absent_one_are_left_as_written() -> None:
    """The other direction: nothing is invented for a field nobody wrote, and a word the gateway
    knows is kept — `flag` stays `flag`."""
    parsed = Pipeline.from_dict(
        {"steps": [{"type": "injection_filter", "config": {"action": "flag"}}]}
    )

    assert parsed.steps[0].config == {"action": "flag"}
