"""Request-rate limits per use case / member (FRD-405).

A budget answers *how much* may be consumed over a day or a month. This answers *how fast*, and
the two are genuinely independent: a monthly budget is no protection against a retry loop that
burns it in an afternoon, and a rate limit says nothing about the total.

Authored here, distributed over Kafka, enforced by the gateway. Absence means unlimited — this
must never begin refusing traffic that an installation was serving before it upgraded.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from aira_management.apps.usecases.models import UseCase


class RateLimit(models.Model):
    USE_CASE = "use_case"
    #: **Each member, individually** — one row, one counter per person (2026-08-11).
    #:
    #: The answer to "everybody, but separately" — a fair share per head without listing the
    #: heads, and it keeps applying to people who join afterwards.
    #:
    #: Not a variant of `USE_CASE`: that one is a **shared pot**, where the first caller to arrive
    #: can spend all of it. Two different governance decisions, and neither substitutes for the
    #: other.
    EACH_MEMBER = "each_member"
    #: A ``member`` scope naming one person **was removed on the owner's decision (2026-08-14)**:
    #: singling somebody out is not a governance decision this product wants to make easy. What is
    #: left says everything an administrator needs — a shared pot, or the same allowance for
    #: everybody — and neither substitutes for the other. Existing rows are deleted by migration
    #: rather than left in place: a stored scope that no longer resolves is a rule enforced by
    #: nothing and visible in nothing.
    SCOPE_CHOICES = [
        (USE_CASE, "Use case"),
        (EACH_MEMBER, "Each member"),
    ]

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="rate_limits")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    subject = models.CharField(max_length=255, blank=True)  # member username; empty for use_case
    limit_rpm = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(1_000_000)],
        help_text="Sustained requests per minute",
    )
    # How many may arrive at once. Bursts are ordinary traffic — a client opening a page, a batch
    # job starting — and refusing them would make the gateway feel broken for normal use. What a
    # limit needs to stop is the sustained flood behind them.
    burst = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(1_000_000)],
        help_text="Requests allowed at once; 0 uses the per-minute limit",
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scope", "subject"]
        constraints = [
            models.UniqueConstraint(fields=["use_case", "scope", "subject"], name="uq_rate_limit")
        ]

    def __str__(self) -> str:
        who = self.subject or self.use_case.slug
        return f"rate limit {self.scope}:{who} ({self.limit_rpm}/min)"
