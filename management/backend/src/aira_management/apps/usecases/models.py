"""Use-case, membership and group-grant models (`FRD-202`, `FRD-209`).

The ``slug`` is the stable identifier used by the gateway selector (``/uc/<slug>``) and the
Keycloak group ``/use-cases/<slug>``, so it is restricted to the same charset.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

#: One week: long enough to investigate an incident, short enough that last month's prompts are
#: gone (`FRD-404`).
DEFAULT_RETENTION_DAYS = 7

#: How long a retired use case must stay retired before it may be purged (`FRD-607`).
#:
#: A **decision gap**, not a retention period: a purge possible in the same minute as the deletion
#: is not a second decision, and waiting keeps the tombstone visible to governance meanwhile.
#: Prompts are unaffected — they follow the use case's own `retention_days` either way.
PURGE_AFTER_DAYS = 30

#: The longest description or processing note a use case may carry. Generous for prose, and
#: finite because both travel in every `usecase.upserted` event: the event channel carries about a
#: megabyte, and JSON escapes one character into as many as twelve bytes, so two notes at this
#: bound still leave the event well inside it (`apps.outbox.subscriber`).
MAX_PROSE_LENGTH = 20_000

#: `\Z`, not `$`, which also matches before a trailing newline. The slug is a primary key on the
#: other plane — emitted over Kafka, a group-path suffix, printed into every audit row (`FRD-613`).
slug_validator = RegexValidator(
    regex=r"^[a-z0-9-]+\Z",
    message="Use lowercase letters, digits, and hyphens only.",
)


class UseCaseQuerySet(models.QuerySet["UseCase"]):
    def live(self) -> UseCaseQuerySet:
        """Every use case that has not been retired — the one place that filter is written.

        A soft delete some queries honour and others do not shows a retired use case on exactly the
        screens nobody audited, so every route that reaches use cases or what hangs off them asks
        this, never `deleted_at` itself (`FRD-607`). Purged rows are gone and need no filter.
        """
        return self.filter(deleted_at__isnull=True)

    def retired(self) -> UseCaseQuerySet:
        """The tombstones: retired, and not yet purged."""
        return self.filter(deleted_at__isnull=False)


class UseCase(models.Model):
    slug = models.CharField(max_length=64, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=MAX_PROSE_LENGTH)
    processing_notes = models.TextField(blank=True, max_length=MAX_PROSE_LENGTH)
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
    #: Which catalogued models this use case may call (`FRD-308`). **Empty means none**: a use case
    #: reaches what somebody released for it, inside the installation's approval (`FRD-307`).
    #: A relation rather than a list of names, so "which use cases would break if I retired this
    #: model" is `model.use_cases.all()` and removing a model cleans up its releases.
    allowed_models = models.ManyToManyField(
        "catalog.Model",
        blank=True,
        related_name="use_cases",
        help_text=(
            "The models this use case may call (FRD-308). Empty means none: a model is released "
            "for a use case, never assumed. Only approved models can be released."
        ),
    )

    #: Whether a model's reasoning comes back and is kept (`FRD-135`). On unless an administrator
    #: turns it off. It is stored exactly as the answer is — same payload, `store_payloads` gate,
    #: retention and read check; there is no second storage path, so off is the choice for a use
    #: case whose reasoning must not be kept.
    include_reasoning = models.BooleanField(
        default=True,
        help_text=(
            "Return the model's reasoning to callers and store it with the answer (FRD-135). "
            "On by default; turn it off where reasoning must not be kept, since it can restate "
            "the prompt verbatim."
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
    #: Who, **inside this use case**, may open a stored prompt and answer (`FRD-622` FR-1).
    #:
    #: A choice and not a pair of booleans, because it is one question with three answers and two
    #: booleans can spell combinations that mean nothing. `store_payloads` stays beside it: whether
    #: anything is written is a different question from who may read it, and it is the one
    #: retention reads as a period of zero.
    #:
    #: It restricts **content**, never rows: which requests a member sees at all is what `OWN`
    #: decides, and the figures a use case needs to run itself — status, latency, cost — stay
    #: where they were in every mode.
    PAYLOAD_VISIBILITY_MEMBERS = "members"
    PAYLOAD_VISIBILITY_OWN = "own"
    PAYLOAD_VISIBILITY_OVERSIGHT = "oversight"
    PAYLOAD_VISIBILITIES = [
        (PAYLOAD_VISIBILITY_MEMBERS, "Every member of the use case"),
        (PAYLOAD_VISIBILITY_OWN, "Administrators, and each person's own requests"),
        (PAYLOAD_VISIBILITY_OVERSIGHT, "Oversight roles only"),
    ]
    payload_visibility = models.CharField(
        max_length=16,
        choices=PAYLOAD_VISIBILITIES,
        default=PAYLOAD_VISIBILITY_MEMBERS,
        help_text=(
            "Who inside this use case may read stored prompts and responses, and whose figures "
            "per person they see (FRD-622, FRD-603). 'members': everybody in the use case, "
            "everything. 'own': administrators of the use case read all of it, everybody else "
            "only their own requests and figures. 'oversight': nobody in the use case, not even "
            "its administrators — only roles that may read any content (payload.read_any: Global "
            "Administrator and IT Security, and any role given it); each reader sees their own "
            "figures. The use case's totals are unaffected."
        ),
    )
    retention_days = models.PositiveSmallIntegerField(
        default=DEFAULT_RETENTION_DAYS,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
        help_text="Days that stored prompts and responses are kept. Metadata is retained.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: When this use case was retired. **Deleting is a tombstone, never a removal** (`FRD-607`):
    #: the gateway keeps the traffic in its audit trail, and what the use case was for, its
    #: releases, storage settings and members live here — the context that makes that traffic
    #: evidence. A Global Administrator decides later whether the row is ever removed. Its slug
    #: stays taken, so a re-created use case cannot inherit the old one's audit history.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    #: Who retired it. A string, not a foreign key: the record has to outlive the account.
    deleted_by = models.CharField(max_length=150, blank=True)

    objects = UseCaseQuerySet.as_manager()

    class Meta:
        ordering = ["slug"]
        permissions = [("manage_members", "Can manage use-case members")]

    def __str__(self) -> str:
        return self.slug

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


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

    The path is whatever the realm uses; AIRA imposes no naming convention and never writes to the
    directory, so who is in the group stays the identity provider's answer. The object permissions
    go to a Django group mirroring the path, so guardian resolves user and group permissions in one
    query and every predicate (`scope_queryset`, `may_admin`, `may_manage`) works unchanged.
    """

    ADMIN = UseCaseMembership.ADMIN
    USER = UseCaseMembership.USER
    #: The same two values a user grant has.
    ROLE_CHOICES = UseCaseMembership.ROLE_CHOICES

    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="group_grants")
    #: Keycloak's group path, exactly as the token reports it.
    group_path = models.CharField(max_length=255)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=USER)
    #: Who granted it: a review asks who opened a door.
    granted_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["use_case", "group_path"], name="unique_group_grant")
        ]

    def __str__(self) -> str:
        return f"{self.group_path} in {self.use_case} ({self.role})"
