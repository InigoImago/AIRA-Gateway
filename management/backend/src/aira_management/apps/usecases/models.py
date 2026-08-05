"""Use-case + membership models (FRD-202).

The ``slug`` is the stable identifier used by the gateway selector (``/uc/<slug>``) and the
Keycloak group ``/use-cases/<slug>``, so it is restricted to the same charset.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

# One week. Long enough to investigate an incident, short enough that a prompt someone typed
# last month is simply not there any more (FRD-404).
DEFAULT_RETENTION_DAYS = 7

slug_validator = RegexValidator(
    regex=r"^[a-z0-9-]+$",
    message="Use lowercase letters, digits, and hyphens only.",
)


class UseCase(models.Model):
    slug = models.CharField(max_length=64, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    processing_notes = models.TextField(blank=True)
    retention_days = models.PositiveSmallIntegerField(
        default=DEFAULT_RETENTION_DAYS,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
        help_text="Days that stored prompts and responses are kept. Metadata is retained.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        permissions = [("manage_members", "Can manage use-case members")]

    def __str__(self) -> str:
        return self.slug


class UseCaseMembership(models.Model):
    ADMIN = "admin"
    USER = "user"
    ROLE_CHOICES = [(ADMIN, "Admin"), (USER, "User")]

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="use_case_memberships"
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=USER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["use_case", "user"], name="unique_membership")
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.use_case} ({self.role})"
