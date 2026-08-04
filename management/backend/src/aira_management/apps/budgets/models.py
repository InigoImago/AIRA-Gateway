"""Budget definitions per use case / member (FRD-400).

Authored in Management and distributed to the gateway, which accounts usage and enforces the
limits (FRD-401). Limits are in tokens and/or request count over a day/month period.
"""

from __future__ import annotations

from django.db import models

from aira_management.apps.usecases.models import UseCase


class Budget(models.Model):
    USE_CASE = "use_case"
    MEMBER = "member"
    SCOPE_CHOICES = [(USE_CASE, "Use case"), (MEMBER, "Member")]

    DAY = "day"
    MONTH = "month"
    PERIOD_CHOICES = [(DAY, "Daily"), (MONTH, "Monthly")]

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="budgets")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    subject = models.CharField(max_length=255, blank=True)  # member username; empty for use_case
    period = models.CharField(max_length=8, choices=PERIOD_CHOICES, default=MONTH)
    limit_tokens = models.PositiveIntegerField(null=True, blank=True)
    limit_requests = models.PositiveIntegerField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scope", "subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["use_case", "scope", "subject", "period"], name="uq_budget"
            )
        ]

    def __str__(self) -> str:
        who = self.subject or self.use_case.slug
        return f"budget {self.scope}:{who} ({self.period})"
