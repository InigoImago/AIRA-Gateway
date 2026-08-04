"""Serializer + validation for pipeline configs (FRD-300/303)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_management.apps.pipelines.models import PipelineConfig

STEP_TYPES = {"injection_filter", "allow_check", "model_route"}


class PipelineConfigSerializer(serializers.ModelSerializer[PipelineConfig]):
    class Meta:
        model = PipelineConfig
        fields = ["steps", "fallback_models", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_steps(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise serializers.ValidationError("steps must be a list.")
        for step in value:
            if not isinstance(step, dict) or step.get("type") not in STEP_TYPES:
                raise serializers.ValidationError(
                    f"Each step needs a type in {sorted(STEP_TYPES)}."
                )
            if "config" in step and not isinstance(step["config"], dict):
                raise serializers.ValidationError("step.config must be an object.")
        return value

    def validate_fallback_models(self, value: Any) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("fallback_models must be a list of strings.")
        return value
