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
    store_payloads = models.BooleanField(
        default=True,
        help_text="Store prompts and responses at all. Off means nothing is written.",
    )
    tools_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Let this use case declare functions for the model to call (FRD-131). Off by default: "
            "a use case that summarises documents has no business declaring functions, and the "
            "smallest set that needs tool calling is the right set to have it."
        ),
    )
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


class UseCaseGroupGrant(models.Model):
    """Access granted to a **Keycloak group** rather than to a person (`FRD-209`).

    The path is whatever the realm uses — `/ai/kundenservice`, `/abteilungen/vertrieb/nord`. AIRA
    imposes no naming convention on somebody else's directory, and never writes to it: who is in
    the group stays the identity provider's answer, which is the entire point. A grant that reaches
    nobody today may reach somebody tomorrow without anything here changing.

    The object permissions are assigned to a **Django group** mirroring the path, so
    `django-guardian` resolves user-and-group permissions in one query and every existing predicate
    (`scope_queryset`, `may_admin`, `may_manage`) keeps working untouched. A second permission path
    beside guardian's would be a second chance to forget one.
    """

    ADMIN = UseCaseMembership.ADMIN
    USER = UseCaseMembership.USER
    #: The same two values a user grant has. A third level is a real idea and not this one.
    ROLE_CHOICES = UseCaseMembership.ROLE_CHOICES

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="group_grants")
    #: Keycloak's group path, exactly as the token reports it.
    group_path = models.CharField(max_length=255)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=USER)
    #: Who granted it, kept for the same reason a suspension keeps its author: a review asks.
    granted_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["use_case", "group_path"], name="unique_group_grant")
        ]

    def __str__(self) -> str:
        return f"{self.group_path} in {self.use_case} ({self.role})"
