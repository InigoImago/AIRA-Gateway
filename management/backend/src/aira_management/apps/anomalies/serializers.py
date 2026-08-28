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

    def _effective(self, attrs: dict[str, Any], field: str, default: Any = None) -> Any:
        """What this field will hold **after** the save: the incoming value, or the stored one.

        Every check below is about a *rule*, and a `PATCH` carries an *edit*. Reading the edit
        alone made each check answer about a rule nobody has: `kind` fell back to `refusal_rate`
        and `action` to `alert` whatever the row said, which is the failure this project names as
        *a default on a discriminator stops discriminating* — here on the two discriminators that
        decide every other check in the method.

        Three consequences, all measured on 2026-08-26:

        - **Every** partial edit was refused, with a message about `min_sample` — a field the
          caller had not sent — because the defaulted kind demanded a sample the body did not
          carry. `PATCH` is what the console's own client method is built on, and its docstring
          says so: *"a rule has thirteen fields and most edits touch one of them"*.
        - A partial edit that did carry `min_sample` **cleared `action_minutes`**, because a
          `throttle` rule read as an `alert` falls into the branch that removes an expiry which
          would be meaningless on an alert. The gateway then refuses to enforce a rule with no
          expiry — correctly, `service._act` answers `detected_not_enforced` — so an automatic
          throttle became a detect-only rule that the console still displays as throttling. The
          badge-wearing absent control, arriving through a rename.
        - A `spend_spike` threshold below 100 was accepted, because the ratio check asked about a
          rate. That rule then fires every window forever, which is the alert people mute.

        The console sends the whole object and works around this — `rule-form.ts` says *"sent
        whether or not it was editable, because … a PATCH that omitted it would be checked against
        the default instead"*. A workaround in one client is not a property of the endpoint.
        """
        if field in attrs:
            return attrs[field]
        if self.instance is not None:
            return getattr(self.instance, field, default)
        return default

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        kind = RuleKind(self._effective(attrs, "kind", RuleKind.REFUSAL_RATE))
        action = RuleAction(self._effective(attrs, "action", RuleAction.ALERT))
        threshold = self._effective(attrs, "threshold")

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
            minutes = self._effective(attrs, "action_minutes")
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
            if not self._effective(attrs, "throttle_rpm"):
                raise serializers.ValidationError(
                    {
                        "throttle_rpm": (
                            "Required for 'throttle': a rate reduced to nothing is a block."
                        )
                    }
                )
        elif attrs.get("throttle_rpm") is not None:
            # Only what the caller **sent** is refused; a value merely left over on the row is
            # cleared below. Refusing the leftover would make "change this throttle to an alert"
            # impossible without also naming a field the caller has no reason to mention.
            raise serializers.ValidationError(
                {"throttle_rpm": f"'{action}' does not reduce a rate."}
            )
        else:
            # An action that does not reduce a rate keeps no rate. Left behind, it is a number
            # the row carries, the event ships and nothing reads — the same reason an `alert`
            # keeps no expiry.
            attrs["throttle_rpm"] = None

        if needs_parameter(kind):
            if not self._effective(attrs, "parameter"):
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
        else:
            # And a kind edited away from `payload_size` keeps no byte figure, for the same reason
            # as the two above: a stale number on the row is one a reader takes for configuration.
            attrs["parameter"] = None

        if not needs_sample(kind):
            # A credential used from a new address is one observation, not a proportion. Requiring
            # a sample of twenty would be requiring twenty leaks.
            attrs["min_sample"] = 0
        elif self._effective(attrs, "min_sample", 0) < 1:
            raise serializers.ValidationError(
                {
                    "min_sample": (
                        "At least 1: without a sample floor, one refused request out of one is "
                        "100 percent."
                    )
                }
            )
        return attrs
