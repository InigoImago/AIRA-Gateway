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

#: How long a retired use case must stay retired before it may be purged (`FRD-607`).
#:
#: The number is a **decision gap**, not a retention period: a purge that can be carried out in the
#: same minute as the deletion is not a second decision, it is the same one with an extra click.
#: Thirty days is long enough that erasing a record requires deliberately coming back for it — and
#: coming back means the tombstone was visible in the retired list for a month, to every governance
#: role, while somebody waited.
#:
#: It bounds *removal of the record*, and has nothing to do with prompts: those go on the use
#: case's own `retention_days` clock whether it is retired or not, which is the half of this
#: feature the GDPR asks about (`FRD-404`).
PURGE_AFTER_DAYS = 30

#: **`\Z`, not `$`.** Python's `$` also matches before a trailing newline, so `"kundenservice\n"`
#: satisfied a validator whose whole job is that a slug carries nothing but `[a-z0-9-]` — and this
#: string is a **primary key on the other plane** (`FRD-613`, `LESSONS.md`): it is emitted over
#: Kafka, written into the gateway's read model, used as a group-path suffix and printed into every
#: audit row. The same one-character correction is applied to `group_path` and to the gateway's own
#: selector, because the trap is the anchor rather than the pattern.
slug_validator = RegexValidator(
    regex=r"^[a-z0-9-]+\Z",
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

    #: Whether a model's reasoning comes back and is kept (`FRD-135`).
    #:
    #: **Off**, because reasoning is content of exactly the kind `ADR-0016` reasoned about: the
    #: sensitive part and the useful part are the same part. On, it travels in the response and is
    #: stored the way the answer is — same payload, same `store_payloads` gate, same retention,
    #: same role check on reading. There is deliberately no second storage path: one would be a
    #: second retention bug waiting to be found.
    include_reasoning = models.BooleanField(
        default=False,
        help_text=(
            "Return the model's reasoning to callers and store it with the answer (FRD-135). "
            "Off by default: reasoning can restate the prompt verbatim."
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

    #: When this use case was retired, and by whom. **Deleting is a tombstone, never a removal.**
    #:
    #: The threat is stated plainly by the owner: *"somebody uses a use case for the wrong
    #: purposes, compromises it, and deletes the use case."* Until this existed, the person best
    #: placed to do that was the one allowed to: `perform_destroy` was open to a **use-case
    #: administrator**, and it took the row and every membership with it. The traffic survived in
    #: the gateway's audit trail on purpose (`FRD-404` §4.1) — and survived *context-free*, because
    #: what the use case was **for**, which models it had released, whether it stored prompts and
    #: who its members were all lived here and were gone.
    #:
    #: So the row stays, unreachable and unservable, and a **Global Administrator** decides later
    #: whether it is ever really removed. Two different acts by two different roles, which is the
    #: whole point: the compromised party can retire their use case and cannot erase it.
    #:
    #: Its slug stays taken. That is deliberate: a re-created `kundenservice` inheriting the audit
    #: history of the deleted one is the same evidence problem with extra steps.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    #: The subject who retired it. A string rather than a foreign key, because the record has to
    #: outlive the account: an operator who has left is exactly the one an investigation asks about.
    deleted_by = models.CharField(max_length=150, blank=True)

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
