"""A pipeline's vocabulary and bounds, in the one place both planes read (`FRD-300`, `ADR-0018`).

Management refuses a configuration outside these where it is authored. The gateway applies the
same bounds to what it reads, because its read-model is also reachable by a direct write and by
`pipeline:dryRun`'s unvalidated body — so two copies of each number would be two answers to one
question, and the plane keeping the looser one is the one on the request path.

**The closed vocabularies are the ones a typo turns into the opposite of what was typed.** The
gateway blocks only on the exact word `block` and reads a scope it does not know as the narrower
one, so `"Block"` saved as a filter's action lets every injection through as flagged. They are
refused where they are written, by name, from these sets.

Not here: the injection patterns' count and length, which belong to the pattern rule in
:mod:`aira_common.patterns`.
"""

from __future__ import annotations

from enum import StrEnum

from aira_common.model_names import MAX_MODEL_NAME

#: How many steps one pipeline runs before dispatch.
MAX_STEPS = 32
#: The length of a chain — the use case's own and each step's (`FRD-302` FR-7). One bound: a step
#: allowed a longer chain than the dispatch would spend more on deciding than on answering.
MAX_FALLBACK_MODELS = 16
#: A model a pipeline names is a catalogue name.
MAX_MODEL_LENGTH = MAX_MODEL_NAME
#: Every operator-authored text in a step's config: an `instruction` is a system prompt sent on
#: every request (a bill), a `notice` is shown in front of somebody's answer (their screen).
MAX_TEXT_LENGTH = 4_000
#: The category list is pasted whole into the router's prompt.
MAX_CATEGORIES = 32


class InjectionMode(StrEnum):
    """How an injection filter decides: by pattern, or by asking a model."""

    HEURISTIC = "heuristic"
    LLM = "llm"


class FilterAction(StrEnum):
    """What an injection filter does with an injection: refuse the request, or record it."""

    BLOCK = "block"
    FLAG = "flag"


class ScanScope(StrEnum):
    """What an injection filter reads: the last user message, or every system and user message."""

    USER = "user"
    SYSTEM_USER = "system_user"


class FailurePolicy(StrEnum):
    """What a step does when it cannot decide (`FRD-125`): a filter's undetermined verdict, a
    redactor's failure. Blocking is the default everywhere; allowing is a recorded choice."""

    BLOCK = "block"
    ALLOW = "allow"


#: What one step carries.
STEP_KEYS = frozenset({"type", "config"})
#: The config keys each step type's evaluation reads (`aira_gateway.pipeline.engine.steps`). A key
#: nothing reads is a setting somebody tunes and then wonders why nothing changes.
STEP_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "injection_filter": frozenset(
        {
            "mode",
            "action",
            "scope",
            "patterns",
            "use_builtins",
            "instruction",
            "on_undetermined",
            "model",
            "fallback_models",
        }
    ),
    "model_route": frozenset({"model", "fallback_models", "categories", "default_model", "notice"}),
    "pii_filter": frozenset({"model", "fallback_models", "instruction", "notice", "on_failure"}),
}
#: What one routing category says: its name, what it is for, and the model it routes to.
CATEGORY_KEYS = frozenset({"name", "description", "model"})
