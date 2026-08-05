"""Serializer + validation for pipeline configs (FRD-300/303).

A saved config is executed by the gateway on every request of the use case, so authoring is a
privileged operation *and* an input-validation boundary: the bounds below keep a config from
turning into a denial of service on the shared data plane (ADR-0007).
"""

from __future__ import annotations

import re
from typing import Any

from rest_framework import serializers

from aira_management.apps.pipelines.models import PipelineConfig

STEP_TYPES = {"injection_filter", "allow_check", "model_route"}

MAX_STEPS = 32
MAX_FALLBACK_MODELS = 16
MAX_PATTERNS = 64
MAX_PATTERN_LENGTH = 256
MAX_MODELS = 64
MAX_MODEL_LENGTH = 128
MAX_CATEGORIES = 32
MAX_TEXT_LENGTH = 4_000

# Nested quantifiers ("(a+)+", "(a*)*", "(ab|a)+" …) are the classic trigger for catastrophic
# backtracking: matching one against a long prompt can take exponential time and stall a
# gateway worker. Custom patterns that fail to compile are matched literally by the gateway, so
# rejecting these costs operators nothing they cannot express another way.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*}][^)]*\)\s*[+*]|\([^)]*\|[^)]*\)\s*[+*]")


def _check_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error:
        return  # invalid regex is matched literally by the gateway — harmless
    if _NESTED_QUANTIFIER.search(pattern):
        raise serializers.ValidationError(
            f"Pattern '{pattern}' nests quantifiers, which can hang the gateway. "
            "Rewrite it without a repeated group."
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


def _validate_step_config(step_type: str, config: dict[str, Any]) -> None:
    if step_type == "injection_filter":
        patterns = config.get("patterns", [])
        _check_str_list(patterns, "patterns", MAX_PATTERNS, MAX_PATTERN_LENGTH)
        for pattern in patterns:
            _check_regex(pattern)
        _check_text(config, "instruction")
    elif step_type == "allow_check":
        _check_str_list(config.get("models", []), "models", MAX_MODELS, MAX_MODEL_LENGTH)
    elif step_type == "model_route":
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
