"""Serializer + validation for pipeline configs (FRD-300/303).

A saved config is executed by the gateway on every request of the use case, so authoring is a
privileged operation *and* an input-validation boundary: the bounds below keep a config from
turning into a denial of service on the shared data plane (ADR-0007). The bounds, the closed
vocabularies and the keys each step reads are `aira_common.pipelines`', which the gateway reads too.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from rest_framework import serializers

from aira_common.patterns import MAX_PATTERN_LENGTH, MAX_PATTERNS, catastrophic_reason
from aira_common.pipelines import (
    CATEGORY_KEYS,
    MAX_CATEGORIES,
    MAX_FALLBACK_MODELS,
    MAX_MODEL_LENGTH,
    MAX_STEPS,
    MAX_TEXT_LENGTH,
    STEP_CONFIG_KEYS,
    STEP_KEYS,
    FailurePolicy,
    FilterAction,
    InjectionMode,
    ScanScope,
)
from aira_management.apps.pipelines.models import PipelineConfig

__all__ = [
    "MAX_CATEGORIES",
    "MAX_FALLBACK_MODELS",
    "MAX_MODEL_LENGTH",
    "MAX_PATTERNS",
    "MAX_PATTERN_LENGTH",
    "MAX_STEPS",
    "MAX_TEXT_LENGTH",
    "STEP_TYPES",
    "PipelineConfigSerializer",
    "catastrophic_reason",
]

#: `allow_check` is not a step: which models a use case may call is a property of the use case
#: (`FRD-308`), enforced at every hop rather than once before routing.
STEP_TYPES = {"injection_filter", "model_route", "pii_filter"}


def _check_regex(pattern: str) -> None:
    """Refuse a pattern that could hang a gateway worker, **where it is written**.

    The rule lives in `aira_common.patterns` because the gateway asks it too (`ADR-0018`); refusing
    here tells the operator while they can still rewrite the pattern, in the rule's own words.
    """
    reason = catastrophic_reason(pattern)
    if reason is not None:
        raise serializers.ValidationError(
            f"Pattern '{pattern}' can hang the gateway: {reason}. Rewrite it so the engine has "
            "only one way to match."
        )


def _check_str_list(values: Any, field: str, max_items: int, max_length: int) -> None:
    if not isinstance(values, list):
        raise serializers.ValidationError(f"step.config.{field} must be a list.")
    if len(values) > max_items:
        raise serializers.ValidationError(f"step.config.{field} allows at most {max_items} items.")
    for value in values:
        if not isinstance(value, str) or len(value) > max_length:
            raise serializers.ValidationError(
                f"step.config.{field} must contain strings of at most {max_length} characters."
            )


def _check_text(config: dict[str, Any], field: str, *, where: str = "step.config") -> None:
    value = config.get(field)
    if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
        raise serializers.ValidationError(
            f"{where}.{field} must be text of at most {MAX_TEXT_LENGTH} characters."
        )


def _check_choice(config: dict[str, Any], field: str, vocabulary: type[StrEnum]) -> None:
    """A word from a closed vocabulary, refused by name when it is anything else.

    The gateway reads these leniently — only the exact word `block` blocks, an unknown scope reads
    as the narrower one — so a typo would save, show as configured and mean something else.
    """
    value = config.get(field)
    words = [str(member) for member in vocabulary]
    if value is not None and not (isinstance(value, str) and value in words):
        raise serializers.ValidationError(
            f"step.config.{field} must be one of {words}, not {value!r}."
        )


def _check_flag(config: dict[str, Any], field: str) -> None:
    """A switch is a boolean: the string `"false"` is true to every reader of it."""
    value = config.get(field)
    if value is not None and not isinstance(value, bool):
        raise serializers.ValidationError(f"step.config.{field} must be true or false.")


def _check_model_name(value: Any, where: str) -> None:
    """A model a step names, or nothing yet — the console sends an empty name for an unset one.
    Whether it is released is `validate`'s question; this is whether it is a name at all."""
    if value in (None, ""):
        return
    if not isinstance(value, str) or len(value) > MAX_MODEL_LENGTH:
        raise serializers.ValidationError(
            f"{where} must be a model name of at most {MAX_MODEL_LENGTH} characters."
        )


def _check_keys(block: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Refuse every key the gateway does not read, by name.

    A step's configuration travels to the gateway on every save and is executed on every request:
    a key nothing reads is a setting somebody tunes and then wonders why nothing changes, and an
    unbounded one is an event larger than the bus carries.
    """
    extra = sorted(str(key) for key in block if key not in allowed)
    if extra:
        raise serializers.ValidationError(
            f"{where} does not take {', '.join(repr(key) for key in extra)}: "
            f"it carries {', '.join(sorted(allowed))}."
        )


def _check_step_chain(config: dict[str, Any]) -> None:
    """A step's own fallback chain (`FRD-302` FR-7), bounded where it is authored.

    Same bounds as the use case's dispatch chain, because they are the same thing one level in. The
    *release* check is not here: `validate` collects every model a pipeline names — this chain
    included, through `_models_named_in` — and refuses them together, so one refusal lists them
    all rather than stopping at the first.
    """
    chain = config.get("fallback_models")
    if chain is None:
        return
    _check_str_list(chain, "fallback_models", MAX_FALLBACK_MODELS, MAX_MODEL_LENGTH)


def _validate_step_config(step_type: str, config: dict[str, Any]) -> None:
    _check_keys(config, STEP_CONFIG_KEYS[step_type], f"step.config of {step_type}")
    # Every model-backed step may carry one, so it is checked before the per-type fields.
    _check_step_chain(config)
    _check_model_name(config.get("model"), "step.config.model")
    if step_type == "injection_filter":
        patterns = config.get("patterns", [])
        _check_str_list(patterns, "patterns", MAX_PATTERNS, MAX_PATTERN_LENGTH)
        for pattern in patterns:
            _check_regex(pattern)
        _check_text(config, "instruction")
        _check_choice(config, "mode", InjectionMode)
        _check_choice(config, "action", FilterAction)
        _check_choice(config, "scope", ScanScope)
        _check_flag(config, "use_builtins")
        _check_choice(config, "on_undetermined", FailurePolicy)
    elif step_type == "pii_filter":
        # What to remove, and what the caller is told about it. The notice is put in front of a
        # model's answer, so it is bounded like every operator-authored string.
        _check_text(config, "instruction")
        _check_text(config, "notice")
        _check_choice(config, "on_failure", FailurePolicy)
    elif step_type == "model_route":
        # The sentence the caller is told about the classification (`FRD-309`).
        _check_text(config, "notice")
        _check_model_name(config.get("default_model"), "step.config.default_model")
        categories = config.get("categories", [])
        if not isinstance(categories, list) or len(categories) > MAX_CATEGORIES:
            raise serializers.ValidationError(
                f"step.config.categories must be a list of at most {MAX_CATEGORIES} entries."
            )
        for index, category in enumerate(categories):
            if not isinstance(category, dict):
                raise serializers.ValidationError("Each category must be an object.")
            where = f"step.config.categories[{index}]"
            _check_keys(category, CATEGORY_KEYS, where)
            _check_text(category, "name", where=where)
            _check_text(category, "description", where=where)
            _check_model_name(category.get("model"), f"{where}.model")


def _models_named_in(steps: list[Any], fallbacks: list[Any]) -> list[str]:
    """Every model a pipeline could reach, wherever it is written.

    Mirrored by `aira_gateway.api.pipeline.models_named_in` for an unsaved pipeline posted to the
    dry run; sharing one would put the pipeline schema into the shared library.
    `tools/tests/test_both_planes_find_the_same_models_in_a_pipeline.py` keeps the two in step.
    """
    named: list[str] = []
    for step in steps:
        config = (step.get("config") or {}) if isinstance(step, dict) else {}
        for key in ("model", "default_model"):
            if config.get(key):
                named.append(str(config[key]))
        # A step's own chain (`FRD-302` FR-10). **This is why a step's model call applies no
        # dispatch condition**: an administrator wrote the name and this check saw it. A fallback
        # name nobody checked would be the hole `allow_check`'s removal closed, one field along.
        for fallback in config.get("fallback_models") or []:
            if fallback:
                named.append(str(fallback))
        for category in config.get("categories") or []:
            if isinstance(category, dict) and category.get("model"):
                named.append(str(category["model"]))
    named.extend(str(name) for name in fallbacks if name)
    return list(dict.fromkeys(named))


class PipelineConfigSerializer(serializers.ModelSerializer[PipelineConfig]):
    class Meta:
        model = PipelineConfig
        fields = ["steps", "fallback_models", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_steps(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise serializers.ValidationError("steps must be a list.")
        if len(value) > MAX_STEPS:
            raise serializers.ValidationError(f"A pipeline may have at most {MAX_STEPS} steps.")
        for step in value:
            # A word before it is looked up: membership in a set hashes the value, and a list or
            # an object cannot be hashed.
            step_type = step.get("type") if isinstance(step, dict) else None
            if not isinstance(step_type, str) or step_type not in STEP_TYPES:
                raise serializers.ValidationError(
                    f"Each step needs a type in {sorted(STEP_TYPES)}."
                )
            _check_keys(step, STEP_KEYS, "A step")
            config = step.get("config", {})
            if not isinstance(config, dict):
                raise serializers.ValidationError("step.config must be an object.")
            _validate_step_config(step_type, config)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Every model this pipeline names must be **released to this use case** (`FRD-308`).

        The gateway refuses one at dispatch as well; this is the check that arrives while somebody
        can still fix it. Collected from **everywhere a model can be written** (`_models_named_in`).
        A pipeline that names no model saves even on a use case with nothing released.
        """
        use_case = self.context.get("use_case")
        if use_case is None:
            return attrs
        named = _models_named_in(
            attrs.get("steps", []) or [],
            attrs.get("fallback_models", []) or [],
        )
        if not named:
            return attrs
        released = set(use_case.allowed_models.values_list("name", flat=True))
        withheld = sorted(name for name in named if name not in released)
        if withheld:
            raise serializers.ValidationError(
                {
                    "steps": [
                        f"Not released to '{use_case.slug}': {', '.join(withheld)}. A pipeline may "
                        "only name models the use case may call — release them on the use case "
                        "first, or the gateway refuses them at dispatch."
                    ]
                }
            )
        return attrs

    def validate_fallback_models(self, value: Any) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("fallback_models must be a list of strings.")
        if len(value) > MAX_FALLBACK_MODELS:
            raise serializers.ValidationError(
                f"At most {MAX_FALLBACK_MODELS} fallback models are allowed."
            )
        if any(len(item) > MAX_MODEL_LENGTH for item in value):
            raise serializers.ValidationError(
                f"Model names are limited to {MAX_MODEL_LENGTH} characters."
            )
        return value
