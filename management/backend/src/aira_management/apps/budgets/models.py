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

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from aira_management.apps.usecases.models import UseCase


class Budget(models.Model):
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
    # A ``member`` scope naming one person **was removed on the owner's decision (2026-08-14)**:
    # singling somebody out is not a governance decision this product wants to make easy. What is
    # left says everything an administrator needs — a shared pot, or the same allowance for
    # everybody — and neither substitutes for the other. Existing rows are deleted by migration
    # rather than left in place: a stored scope that no longer resolves is a rule enforced by
    # nothing and visible in nothing. (A plain comment, not a `#:` one: it documents a scope that
    # no longer exists, and a doc-comment attaches to whatever name follows it.)

    #: **The installation's own spend** (`FRD-610`). The residual bucket for what belongs to no use
    #: case: the console's model checks, break-glass keys, demo traffic. Measured on a running
    #: installation, 59 audit rows carried no use case and no allowance could ever see them.
    #:
    #: Not a global cap. A use case's traffic keeps booking against its own budgets; this takes
    #: what the others cannot, which is what turns *"nothing spends outside a bucket"* into a
    #: sentence with a subject for **every** request rather than for most of them.
    INSTALLATION = "installation"
    SCOPE_CHOICES = [
        (USE_CASE, "Use case"),
        (EACH_MEMBER, "Each member"),
        (INSTALLATION, "Installation"),
    ]

    DAY = "day"
    MONTH = "month"
    PERIOD_CHOICES = [(DAY, "Daily"), (MONTH, "Monthly")]

    #: Null for an **installation** budget, which belongs to no use case by definition (`FRD-610`).
    #: Every other scope requires one, and `clean()` refuses the two wrong combinations rather than
    #: leaving a row whose scope and owner disagree.
    use_case = models.ForeignKey(
        UseCase, on_delete=models.CASCADE, related_name="budgets", null=True, blank=True
    )
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
            ),
            # **A NULL is not equal to itself in SQL**, so the constraint above stops policing the
            # moment `use_case` may be null: two installation budgets for the same period would
            # both be accepted, and the gateway would enforce whichever it read first. A second,
            # partial constraint covers exactly the rows the first cannot see.
            models.UniqueConstraint(
                fields=["scope", "period"],
                condition=models.Q(use_case__isnull=True),
                name="uq_installation_budget",
            ),
            # A scope and an owner that disagree, refused where they are stored rather than only
            # where they are typed: `clean()` runs for the API and not for a fixture, a shell, or a
            # migration, and a row whose scope says *installation* while naming a use case is a
            # budget the gateway would match against traffic nobody meant.
            models.CheckConstraint(
                condition=(
                    models.Q(scope="installation", use_case__isnull=True)
                    | (~models.Q(scope="installation") & models.Q(use_case__isnull=False))
                ),
                name="ck_budget_scope_matches_owner",
            ),
        ]

    def clean(self) -> None:
        """The same rule as the constraint above, said where a form can show it."""
        super().clean()
        if self.scope == self.INSTALLATION and self.use_case_id is not None:
            raise ValidationError(
                {"use_case": "An installation budget belongs to no use case; leave it empty."}
            )
        if self.scope != self.INSTALLATION and self.use_case_id is None:
            raise ValidationError({"use_case": f"A '{self.scope}' budget needs a use case."})

    def __str__(self) -> str:
        owner = self.use_case.slug if self.use_case is not None else "installation"
        who = self.subject or owner
        return f"budget {self.scope}:{who} ({self.period})"
