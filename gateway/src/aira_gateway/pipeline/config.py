"""Pipeline configuration schema (FRD-300).

A ``Pipeline`` is an ordered list of steps plus a dispatch fallback chain. It is authored in
Management and distributed to the gateway read-model as JSON; ``Pipeline.from_dict`` parses that
JSON leniently (unknown step types are ignored so old gateways tolerate new config).

**The gateway re-applies Management's bounds.** The read-model is also reachable by a direct write,
a publish onto an unauthenticated broker, and `pipeline:dryRun`'s `pipeline` field, so the
protection cannot sit at one end of the link only (`ADR-0018`). Oversized values are truncated with
a log line rather than dropped: a step on a shortened instruction is degraded, a use case whose
filter vanished is unprotected.

**A value of the wrong type, or a word outside a closed vocabulary, is read the safe way** rather
than carried through for a step to trip over — a stored `steps: null` or a category that is not an
object would make every request of the use case a 500. A field a step cannot use is left out, so the
step runs on its defaults; a word this build does not know is read in the direction that refuses or
reads more, so an `action` spelled `Block` blocks. Each correction is logged once per process.

The dry run's `pipeline` is a body a caller typed, so it is held to the same rules strictly instead:
:func:`problem_in` names the first field that breaks one, and the endpoint answers 400.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aira_common.logging import get_logger
from aira_common.patterns import MAX_PATTERN_LENGTH, MAX_PATTERNS, catastrophic_reason
from aira_common.pipelines import (
    CATEGORY_KEYS,
    MAX_CATEGORIES,
    MAX_FALLBACK_MODELS,
    MAX_MODEL_LENGTH,
    MAX_STEPS,
    MAX_TEXT_LENGTH,
    FailurePolicy,
    FilterAction,
    InjectionMode,
    ScanScope,
)

_log = get_logger("aira_gateway.pipeline")

__all__ = [
    "MAX_CATEGORIES",
    "MAX_FALLBACK_MODELS",
    "MAX_MODEL_LENGTH",
    "MAX_STEPS",
    "MAX_TEXT_LENGTH",
    "Pipeline",
    "PipelineStep",
    "StepType",
    "problem_in",
]

#: The step fields that name a model, and those that carry operator-authored text.
_NAME_FIELDS = ("model", "default_model")
_TEXT_FIELDS = ("instruction", "notice")


class StepType(StrEnum):
    """The steps a pipeline may run before dispatch.

    The former `allow_check` step is now `FRD-308`'s per-use-case model release, enforced at every
    dispatch hop (`ADR-0012` §3). A stored config still naming it degrades to a pipeline without it
    (see `Pipeline.from_dict`), which is correct because the release enforces what it used to.
    """

    INJECTION_FILTER = "injection_filter"
    MODEL_ROUTE = "model_route"
    #: Replaces personal data in the prompt with a trusted model of the use case's choosing
    #: (`FRD-309`). The only step that changes what the caller sent: the request dispatched **and**
    #: the one the audit trail keeps is the rewritten one — the one place `FRD-122`'s "log what was
    #: asked" is relaxed, in favour of the data the step exists to remove.
    PII_FILTER = "pii_filter"


#: Each closed vocabulary a step reads: the words it knows, and how a word it does not know is read.
#: Always the reading that refuses or reads more — a filter whose `action` says `Block` blocks
#: rather than flags, a `scope` of `system-user` reads every message rather than the last, and a
#: policy nobody can name blocks. An unknown `mode` is the heuristic, which needs no model and is
#: never undetermined. An absent field is not corrected: its default is the step's own.
_VOCABULARIES: dict[StepType, dict[str, tuple[type[StrEnum], StrEnum]]] = {
    StepType.INJECTION_FILTER: {
        "mode": (InjectionMode, InjectionMode.HEURISTIC),
        "action": (FilterAction, FilterAction.BLOCK),
        "scope": (ScanScope, ScanScope.SYSTEM_USER),
        "on_undetermined": (FailurePolicy, FailurePolicy.BLOCK),
    },
    StepType.PII_FILTER: {"on_failure": (FailurePolicy, FailurePolicy.BLOCK)},
}


@functools.lru_cache(maxsize=1024)
def _corrected(step: str, where: str, why: str) -> None:
    """Say once per process that a stored value was not used as written. The pipeline is parsed
    for every request; one line per request would bury the one that matters."""
    _log.warning("pipeline_value_corrected", step=step, field=where, why=why)


def _clipped(value: Any, *, where: str, step: str) -> Any:
    """A text field at or under `MAX_TEXT_LENGTH`, saying so when it had to be cut."""
    if not isinstance(value, str) or len(value) <= MAX_TEXT_LENGTH:
        return value
    _log.warning(
        "pipeline_text_truncated", step=step, field=where, length=len(value), limit=MAX_TEXT_LENGTH
    )
    return value[:MAX_TEXT_LENGTH]


def _bounded(config: dict[str, Any], step: str) -> dict[str, Any]:
    """The step's configuration with every operator-authored text held to Management's bounds."""
    bounded = {key: _clipped(value, where=key, step=step) for key, value in config.items()}

    # A step's own fallback chain (`FRD-302` FR-7/FR-12): bounded here as well as in Management,
    # because this read-model is also reachable by a direct write and by `pipeline:dryRun`'s
    # `pipeline` field (`ADR-0018`). Truncated rather than dropped, like every other oversized
    # value: a shortened chain is a step with fewer candidates, an absent one is a step back to a
    # single model.
    chain = bounded.get("fallback_models")
    if isinstance(chain, list):
        if len(chain) > MAX_FALLBACK_MODELS:
            _log.warning(
                "pipeline_step_fallbacks_truncated",
                step=step,
                count=len(chain),
                limit=MAX_FALLBACK_MODELS,
            )
        chain = _strings(chain, step, "fallback_models")
        bounded["fallback_models"] = [
            str(name)[:MAX_MODEL_LENGTH] for name in chain[:MAX_FALLBACK_MODELS] if name
        ]

    categories = bounded.get("categories")
    if isinstance(categories, list):
        if len(categories) > MAX_CATEGORIES:
            _log.warning(
                "pipeline_categories_truncated",
                step=step,
                count=len(categories),
                limit=MAX_CATEGORIES,
            )
        bounded["categories"] = [
            _category(category, step)
            for category in categories[:MAX_CATEGORIES]
            if _is_category(category, step)
        ]
    return bounded


def _typed(config: dict[str, Any], step_type: StepType) -> dict[str, Any]:
    """``config`` with every field a step reads holding the type it reads, or left out."""
    step = str(step_type)
    typed = dict(config)
    for key in _NAME_FIELDS + _TEXT_FIELDS:
        if typed.get(key) is not None and not isinstance(typed[key], str):
            _corrected(step, key, "not text")
            del typed[key]
    for key in ("fallback_models", "categories", "patterns"):
        if key in typed and not isinstance(typed[key], list):
            _corrected(step, key, "not a list")
            del typed[key]
    if "patterns" in typed:
        typed["patterns"] = _strings(typed["patterns"], step, "patterns")
    if "use_builtins" in typed and not isinstance(typed["use_builtins"], bool):
        # Anything but a real `false` keeps the built-in phrasings on: `"false"` is a string.
        _corrected(step, "use_builtins", "not true or false")
        typed["use_builtins"] = True
    for key, (vocabulary, unknown) in _VOCABULARIES.get(step_type, {}).items():
        if key in typed and not _is_word(typed[key], vocabulary):
            _corrected(step, key, f"{typed[key]!r} is not one of {_words(vocabulary)}")
            typed[key] = str(unknown)
    return typed


def _words(vocabulary: type[StrEnum]) -> list[str]:
    return [str(word) for word in vocabulary]


def _is_word(value: Any, vocabulary: type[StrEnum]) -> bool:
    """Whether ``value`` is one of the words — compared as text, so a list or a number is not."""
    return isinstance(value, str) and value in _words(vocabulary)


def _strings(values: list[Any], step: str, where: str) -> list[str]:
    """The text entries of a list; anything else is not a name or a pattern, and is left out."""
    kept = [value for value in values if isinstance(value, str)]
    if len(kept) < len(values):
        _corrected(step, where, "an entry that is not text")
    return kept


def _is_category(category: Any, step: str) -> bool:
    if isinstance(category, dict):
        return True
    _corrected(step, "categories", "an entry that is not an object")
    return False


def _category(category: dict[str, Any], step: str) -> dict[str, Any]:
    """A routing category with every field held to its bound and every known field text."""
    kept: dict[str, Any] = {}
    for key, value in category.items():
        if key in CATEGORY_KEYS and value is not None and not isinstance(value, str):
            _corrected(step, f"categories.{key}", "not text")
            continue
        kept[key] = _clipped(value, where=f"categories.{key}", step=step)
    return kept


@dataclass(frozen=True, slots=True)
class PipelineStep:
    type: StepType
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Pipeline:
    steps: tuple[PipelineStep, ...] = ()
    fallback_models: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether running this changes nothing."""
        return not self.steps and not self.fallback_models

    @classmethod
    def from_dict(cls, data: Any) -> Pipeline:
        """Parse a stored or published pipeline, reading every malformed value the safe way."""
        if not isinstance(data, dict):
            _corrected("pipeline", "pipeline", "not an object")
            return cls()
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            _corrected("pipeline", "steps", "not a list")
            raw_steps = []
        steps: list[PipelineStep] = []
        for raw in raw_steps[:MAX_STEPS]:
            try:
                step_type = StepType(raw["type"])
            except KeyError, ValueError, TypeError:
                continue  # forward-compatible: skip unknown/malformed steps
            config = raw.get("config", {})
            steps.append(
                PipelineStep(
                    type=step_type,
                    config=(
                        _bounded(_typed(config, step_type), str(step_type))
                        if isinstance(config, dict)
                        else {}
                    ),
                )
            )
        chain = data.get("fallback_models") or []
        if not isinstance(chain, list):
            _corrected("pipeline", "fallback_models", "not a list")
            chain = []
        fallbacks = tuple(
            str(m)[:MAX_MODEL_LENGTH]
            for m in _strings(chain, "pipeline", "fallback_models")[:MAX_FALLBACK_MODELS]
            if m
        )
        return cls(steps=tuple(steps), fallback_models=fallbacks)


def problem_in(data: Any) -> str | None:
    """The first thing in a caller's pipeline the rules above would have to correct, named by
    where it is — or ``None``.

    The dry run's body is somebody's typing, and the answer to a mistake there is a 400 that names
    the field, as Management's serializer gives for the same document: correcting it silently would
    show a trace of a pipeline nobody wrote.
    """
    if not isinstance(data, dict):
        return "pipeline must be an object."
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        return "pipeline.steps must be a list."
    if len(steps) > MAX_STEPS:
        return f"pipeline.steps allows at most {MAX_STEPS} steps."
    for index, step in enumerate(steps):
        where = f"pipeline.steps[{index}]"
        if not isinstance(step, dict):
            return f"{where} must be an object."
        if not _is_word(step.get("type"), StepType):
            return f"{where}.type must be one of {_words(StepType)}."
        config = step.get("config") or {}
        if not isinstance(config, dict):
            return f"{where}.config must be an object."
        problem = _config_problem(StepType(step["type"]), config, f"{where}.config")
        if problem is not None:
            return problem
    return _names_problem(data.get("fallback_models", []), "pipeline.fallback_models")


def _config_problem(step_type: StepType, config: dict[str, Any], where: str) -> str | None:
    for key in _NAME_FIELDS:
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > MAX_MODEL_LENGTH):
            return f"{where}.{key} must be a model name of at most {MAX_MODEL_LENGTH} characters."
    for key in _TEXT_FIELDS:
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
            return f"{where}.{key} must be text of at most {MAX_TEXT_LENGTH} characters."
    problem = _names_problem(config.get("fallback_models", []), f"{where}.fallback_models")
    if problem is not None:
        return problem
    for key, (vocabulary, _) in _VOCABULARIES.get(step_type, {}).items():
        value = config.get(key)
        if value is not None and not _is_word(value, vocabulary):
            return f"{where}.{key} must be one of {_words(vocabulary)}, not {value!r}."
    if step_type is StepType.INJECTION_FILTER:
        return _patterns_problem(config, where)
    if step_type is StepType.MODEL_ROUTE:
        return _categories_problem(config.get("categories", []), f"{where}.categories")
    return None


def _names_problem(names: Any, where: str) -> str | None:
    if not isinstance(names, list) or len(names) > MAX_FALLBACK_MODELS:
        return f"{where} must be a list of at most {MAX_FALLBACK_MODELS} model names."
    if any(not isinstance(name, str) or len(name) > MAX_MODEL_LENGTH for name in names):
        return f"{where} must hold model names of at most {MAX_MODEL_LENGTH} characters."
    return None


def _patterns_problem(config: dict[str, Any], where: str) -> str | None:
    if not isinstance(config.get("use_builtins", True), bool):
        return f"{where}.use_builtins must be true or false."
    patterns = config.get("patterns", [])
    if not isinstance(patterns, list) or len(patterns) > MAX_PATTERNS:
        return f"{where}.patterns must be a list of at most {MAX_PATTERNS} patterns."
    for pattern in patterns:
        if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN_LENGTH:
            return f"{where}.patterns must hold text of at most {MAX_PATTERN_LENGTH} characters."
        reason = catastrophic_reason(pattern)
        if reason is not None:
            return f"{where}.patterns: {pattern!r} can hang the gateway: {reason}."
    return None


def _categories_problem(categories: Any, where: str) -> str | None:
    if not isinstance(categories, list) or len(categories) > MAX_CATEGORIES:
        return f"{where} must be a list of at most {MAX_CATEGORIES} categories."
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            return f"{where}[{index}] must be an object."
        for key in CATEGORY_KEYS:
            value = category.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
                return (
                    f"{where}[{index}].{key} must be text of at most {MAX_TEXT_LENGTH} characters."
                )
    return None
