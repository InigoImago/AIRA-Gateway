"""Serializer + validation for anomaly rules (FRD-500).

Validation lives **where the rule is written**, not where it runs — the same rule as `FRD-114`'s
catalog declarations. A threshold the engine cannot evaluate is a rule that looks configured and
does nothing, and the author is the only person in a position to fix it.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from aira_common.anomalies import (
    MAX_ACTION_MINUTES,
    MIN_ACTION_MINUTES,
    PARAMETER_MEANING,
    RATE_KINDS,
    RATIO_KINDS,
    RuleAction,
    RuleKind,
    needs_parameter,
    needs_sample,
    needs_throttle_rate,
)
from aira_management.apps.anomalies.models import AnomalyRule


class AnomalyRuleSerializer(serializers.ModelSerializer[AnomalyRule]):
    #: Present so a caller can tell a global rule from a use-case one without inspecting a null.
    use_case: serializers.SlugRelatedField[Any] = serializers.SlugRelatedField(
        slug_field="slug", read_only=True
    )
    is_global = serializers.BooleanField(read_only=True)

    class Meta:
        model = AnomalyRule
        fields = [
            "id",
            "use_case",
            "is_global",
            "name",
            "kind",
            "window_minutes",
            "threshold",
            "parameter",
            "min_sample",
            "action",
            "target",
            "action_minutes",
            "throttle_rpm",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "use_case", "is_global", "created_at", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        kind = RuleKind(attrs.get("kind", RuleKind.REFUSAL_RATE))
        action = RuleAction(attrs.get("action", RuleAction.ALERT))
        threshold = attrs.get("threshold")

        # A share cannot exceed the whole. Accepted up to 100 rather than below it, because
        # "every request was refused" is a real and interesting state.
        if kind in RATE_KINDS and threshold is not None and threshold > 100:
            raise serializers.ValidationError(
                {"threshold": "A share of requests cannot exceed 100 percent."}
            )
        # A ratio at or below 100 % fires on traffic that did not grow at all — which is every
        # window, forever. The alert that never stops is the one people mute.
        if kind in RATIO_KINDS and threshold is not None and threshold <= 100:
            raise serializers.ValidationError(
                {
                    "threshold": (
                        "A spike is a multiple of the previous window, so it must be above "
                        "100 percent — 200 means twice as much."
                    )
                }
            )

        if action in (RuleAction.THROTTLE, RuleAction.BLOCK):
            minutes = attrs.get("action_minutes")
            if minutes is None:
                raise serializers.ValidationError(
                    {
                        "action_minutes": (
                            f"Required for '{action}': an automatic action with no expiry is an "
                            "outage with a good reason."
                        )
                    }
                )
            if not MIN_ACTION_MINUTES <= minutes <= MAX_ACTION_MINUTES:
                raise serializers.ValidationError(
                    {
                        "action_minutes": (
                            f"Between {MIN_ACTION_MINUTES} and {MAX_ACTION_MINUTES} minutes."
                        )
                    }
                )
        else:
            # An expiry on an `alert` would read as though the alert stopped applying.
            attrs["action_minutes"] = None

        if needs_throttle_rate(action):
            if not attrs.get("throttle_rpm"):
                raise serializers.ValidationError(
                    {
                        "throttle_rpm": (
                            "Required for 'throttle': a rate reduced to nothing is a block."
                        )
                    }
                )
        elif attrs.get("throttle_rpm") is not None:
            raise serializers.ValidationError(
                {"throttle_rpm": f"'{action}' does not reduce a rate."}
            )

        if needs_parameter(kind):
            if not attrs.get("parameter"):
                raise serializers.ValidationError(
                    {
                        "parameter": (
                            f"Required for '{kind}': the threshold is a share of requests, and "
                            f"this is the {PARAMETER_MEANING[kind]} the share is measured against."
                        )
                    }
                )
        elif attrs.get("parameter") is not None:
            # Refused rather than ignored. A number a rule accepts and never reads is a setting
            # somebody will tune, and then wonder why nothing changes (`FRD-124`).
            raise serializers.ValidationError({"parameter": f"'{kind}' takes no second number."})

        if not needs_sample(kind):
            # A credential used from a new address is one observation, not a proportion. Requiring
            # a sample of twenty would be requiring twenty leaks.
            attrs["min_sample"] = 0
        elif attrs.get("min_sample", 0) < 1:
            raise serializers.ValidationError(
                {
                    "min_sample": (
                        "At least 1: without a sample floor, one refused request out of one is "
                        "100 percent."
                    )
                }
            )
        return attrs
