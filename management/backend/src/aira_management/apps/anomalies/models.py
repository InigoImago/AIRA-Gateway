"""What an installation considers abnormal, said out loud (FRD-500).

The gateway records everything (`FRD-122`) and nobody is watching. This is the rule: what to
watch, over what window, above what threshold, and what to do then. `FRD-501` evaluates it;
`FRD-503` acts on it.

Authored here, distributed over Kafka, evaluated by the gateway — the same path as budgets
(`FRD-400`) and rate limits (`FRD-405`), deliberately, because a second distribution mechanism
would be a second thing to get wrong during a rolling deploy.

**Absence means no detection.** An installation that authors no rule behaves exactly as it does
today; this must never begin refusing traffic somebody was already serving.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from aira_common.anomalies import (
    DEFAULT_MIN_SAMPLE,
    MAX_ACTION_MINUTES,
    MAX_WINDOW_MINUTES,
    MIN_ACTION_MINUTES,
    MIN_WINDOW_MINUTES,
    RuleAction,
    RuleKind,
    RuleTarget,
)
from aira_management.apps.usecases.models import UseCase


class AnomalyRule(models.Model):
    """One statement of what abnormal looks like.

    ``use_case`` is nullable: a rule with none is **global**, and only IT Security or a Global
    Administrator may author one (`FRD-500` FR-8) — a global rule's effects land on use cases its
    author may not be able to see.
    """

    KIND_CHOICES = [(kind.value, kind.value) for kind in RuleKind]
    ACTION_CHOICES = [(action.value, action.value) for action in RuleAction]
    TARGET_CHOICES = [(target.value, target.value) for target in RuleTarget]

    use_case = models.ForeignKey(
        UseCase,
        on_delete=models.CASCADE,
        related_name="anomaly_rules",
        null=True,
        blank=True,
        help_text="Empty means the rule applies everywhere.",
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    window_minutes = models.PositiveIntegerField(
        default=15,
        validators=[MinValueValidator(MIN_WINDOW_MINUTES), MaxValueValidator(MAX_WINDOW_MINUTES)],
    )
    #: Percent for rate and ratio kinds; a count for event kinds. What it *means* comes from the
    #: kind (`aira_common.anomalies.threshold_unit`), never from a second column.
    threshold = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    #: Below this many requests in the window, a rate or a ratio says nothing: one refusal out of
    #: one request is 100 %. Ignored by kinds that are not proportions of anything.
    min_sample = models.PositiveIntegerField(default=DEFAULT_MIN_SAMPLE)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, default=RuleAction.ALERT)
    target = models.CharField(max_length=16, choices=TARGET_CHOICES, default=RuleTarget.SUBJECT)
    #: How long a `throttle` or `block` lasts. Required for those, meaningless for `alert` — an
    #: automatic action with no expiry is an outage with a good reason (`ADR-0014` §2).
    action_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_ACTION_MINUTES), MaxValueValidator(MAX_ACTION_MINUTES)],
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["use_case_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["use_case", "name"],
                name="uq_anomaly_rule_name",
                # A global rule has no use case, and NULLs do not collide in a unique index — so
                # the global namespace needs its own constraint or two global rules could share a
                # name and become indistinguishable in an incident.
                condition=models.Q(use_case__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["name"],
                name="uq_global_anomaly_rule_name",
                condition=models.Q(use_case__isnull=True),
            ),
        ]

    @property
    def is_global(self) -> bool:
        return self.use_case_id is None

    def __str__(self) -> str:
        where = self.use_case.slug if self.use_case is not None else "global"
        return f"{self.kind} rule '{self.name}' ({where}, {self.action})"
