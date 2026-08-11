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
    prompt_caching_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Let the gateway mark this use case's stable prefix — tool declarations and system "
            "instruction — as cacheable at the provider (FRD-133). Off by default: on Vertex the "
            "cache scope is the whole organisation, so a use case whose system prompt is itself "
            "confidential should not be opted in by somebody else's cost decision."
        ),
    )
    #: Which catalogued models this use case may call (`FRD-308`).
    #:
    #: **Empty means none, and that is the owner's decision** (2026-08-11): a use case reaches the
    #: models somebody released for it, not everything the installation happens to have approved.
    #: `FRD-307` is the outer boundary — a Global Administrator decides what may be used *here at
    #: all* — and this is the inner one, which the use case's own administrator owns.
    #:
    #: A **relation** rather than a list of names, and the reason is a question somebody asks:
    #: "which use cases would break if I retired this model". With a JSON list that is a
    #: containment query written differently on SQLite and Postgres, which `FRD-505` already paid
    #: for once; with a relation it is `model.use_cases.all()`, and removing a model from the
    #: catalog cleans the releases up rather than leaving names that resolve to nothing.
    allowed_models = models.ManyToManyField(
        "catalog.Model",
        blank=True,
        related_name="use_cases",
        help_text=(
            "The models this use case may call (FRD-308). Empty means none: a model is released "
            "for a use case, never assumed. Only approved models can be released."
        ),
    )

    PROMPT_CACHE_TTLS = [("5m", "5 minutes"), ("1h", "1 hour")]
    prompt_cache_ttl = models.CharField(
        max_length=4,
        choices=PROMPT_CACHE_TTLS,
        default="5m",
        help_text=(
            "How long the provider should keep this use case's cached prefix (FRD-133). The "
            "trade-off is real and only measurement settles it: an hour costs about twice the "
            "ordinary input price to write against roughly a quarter extra for five minutes, and "
            "pays for itself only if the gap between turns regularly exceeds five minutes."
        ),
    )
    restrict_members_to_own_requests = models.BooleanField(
        default=False,
        help_text=(
            "Show each use-case *user* only the requests they made themselves. An administrator "
            "of the use case still sees all of them. Default off, which is the behaviour that "
            "already existed — this is a restriction an administrator may impose, not a "
            "permission that was previously assumed."
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
