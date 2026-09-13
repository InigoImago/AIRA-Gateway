"""Serializer + validation for pipeline configs (FRD-300/303).

A saved config is executed by the gateway on every request of the use case, so authoring is a
privileged operation *and* an input-validation boundary: the bounds below keep a config from
turning into a denial of service on the shared data plane (ADR-0007).
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_common.patterns import catastrophic_reason
from aira_management.apps.pipelines.models import PipelineConfig

#: `allow_check` is not a step: which models a use case may call is a property of the use case
#: (`FRD-308`), enforced at every hop rather than once before routing.
STEP_TYPES = {"injection_filter", "model_route", "pii_filter"}

MAX_STEPS = 32
MAX_FALLBACK_MODELS = 16
MAX_PATTERNS = 64
MAX_PATTERN_LENGTH = 256
MAX_MODEL_LENGTH = 128
MAX_CATEGORIES = 32
MAX_TEXT_LENGTH = 4_000

#: What a *blocking* LLM filter does when its classifier reaches no verdict, and a PII filter when
#: it fails (`FRD-125`). Validated where it is authored: the gateway treats anything but "allow" as
#: blocking, so a typo would be safe and silently mean the opposite of what was typed.
UNDETERMINED_POLICIES = ("block", "allow")


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


def _check_text(config: dict[str, Any], field: str) -> None:
    value = config.get(field)
    if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
        raise serializers.ValidationError(
            f"step.config.{field} must be text of at most {MAX_TEXT_LENGTH} characters."
        )


def _check_policy(config: dict[str, Any], field: str) -> None:
    policy = config.get(field)
    if policy is not None and policy not in UNDETERMINED_POLICIES:
        raise serializers.ValidationError(
            f"step.config.{field} must be one of {list(UNDETERMINED_POLICIES)}, not '{policy}'."
        )


def _validate_step_config(step_type: str, config: dict[str, Any]) -> None:
    if step_type == "injection_filter":
        patterns = config.get("patterns", [])
        _check_str_list(patterns, "patterns", MAX_PATTERNS, MAX_PATTERN_LENGTH)
        for pattern in patterns:
            _check_regex(pattern)
        _check_text(config, "instruction")
        _check_policy(config, "on_undetermined")
    elif step_type == "pii_filter":
        # What to remove, and what the caller is told about it. The notice is put in front of a
        # model's answer, so it is bounded like every operator-authored string.
        _check_text(config, "instruction")
        _check_text(config, "notice")
        _check_policy(config, "on_failure")
    elif step_type == "model_route":
        # The sentence the caller is told about the classification (`FRD-309`).
        _check_text(config, "notice")
        categories = config.get("categories", [])
        if not isinstance(categories, list) or len(categories) > MAX_CATEGORIES:
            raise serializers.ValidationError(
                f"step.config.categories must be a list of at most {MAX_CATEGORIES} entries."
            )
        for category in categories:
            if not isinstance(category, dict):
                raise serializers.ValidationError("Each category must be an object.")
            for field in ("name", "description", "model"):
                _check_text(category, field)


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
            if not isinstance(step, dict) or step.get("type") not in STEP_TYPES:
                raise serializers.ValidationError(
                    f"Each step needs a type in {sorted(STEP_TYPES)}."
                )
            config = step.get("config", {})
            if not isinstance(config, dict):
                raise serializers.ValidationError("step.config must be an object.")
            _validate_step_config(str(step["type"]), config)
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
