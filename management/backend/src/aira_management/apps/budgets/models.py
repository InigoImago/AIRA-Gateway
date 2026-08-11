"""Budget definitions per use case / member (FRD-400).

Authored in Management and distributed to the gateway, which accounts usage and enforces the
limits (FRD-401). A limit is a **cost** in the installation currency (FRD-403) and/or a token
or request count, over a day/month period.

Cost is the limit that answers the question a budget is usually asked — token counts differ in
price by more than an order of magnitude between models, so a token cap says little about spend.
The count-based limits remain useful as a volume guard and stay supported.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from aira_management.apps.usecases.models import UseCase


class Budget(models.Model):
    USE_CASE = "use_case"
    MEMBER = "member"
    #: **Each member, individually** — one row, one counter per person (2026-08-11).
    #:
    #: `MEMBER` names somebody; this is the answer to "everybody, but separately", which is what an
    #: administrator wants far more often — a fair share per head without listing the heads, and it
    #: keeps applying to people who join afterwards.
    #:
    #: Not a variant of `USE_CASE`: that one is a **shared pot**, where the first caller to arrive
    #: can spend all of it. Two different governance decisions, and neither substitutes for the
    #: other.
    EACH_MEMBER = "each_member"
    SCOPE_CHOICES = [
        (USE_CASE, "Use case"),
        (EACH_MEMBER, "Each member"),
        (MEMBER, "One member"),
    ]

    DAY = "day"
    MONTH = "month"
    PERIOD_CHOICES = [(DAY, "Daily"), (MONTH, "Monthly")]

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="budgets")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    subject = models.CharField(max_length=255, blank=True)  # member username; empty for use_case
    period = models.CharField(max_length=8, choices=PERIOD_CHOICES, default=MONTH)
    limit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Spend limit for the period, in the installation currency",
    )
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
