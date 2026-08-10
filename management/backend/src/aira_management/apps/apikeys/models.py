"""API-key model — Management is the source of truth for issuance (FRD-205, ADR-0006).

Only the hash of the full key is stored; the plaintext is shown once at issue time and never
persisted. Each key is bound to exactly one use case, which the gateway uses to attribute and
authorize requests made with it (no ``/uc`` selector needed).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from aira_management.apps.usecases.models import UseCase


class ApiKey(models.Model):
    use_case = models.ForeignKey(UseCase, on_delete=models.CASCADE, related_name="api_keys")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    #: Who created it, when that is not the owner (`FRD-604` FR-5).
    #:
    #: **Two different questions.** `owner` is who *answers for* the credential — a technical
    #: account for a team's shared key — and it is the name every audit row carries, correctly: a
    #: row describes what called, not who authorised the credential months earlier. `issued_by` is
    #: the human who created it, and it is the fact that a shared credential otherwise destroys.
    #:
    #: A **string**, not a foreign key, and for the same reason as `UseCaseGroupGrant.granted_by`
    #: and a suspension's `author`: this is a fact about the past. Deleting the person must not
    #: delete the record of what they did, and must not be prevented by it either. Blank means the
    #: owner issued it themselves, which is every key from before this column existed.
    issued_by = models.CharField(max_length=150, blank=True)
    prefix = models.CharField(max_length=32, unique=True, db_index=True)
    key_hash = models.CharField(max_length=64)
    label = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    #: When the key stops working on its own (2026-08-08). **NULL means never** — every key issued
    #: before this existed carries that, and an expiry that cannot be omitted is one somebody sets
    #: to the year 3000. Expiry and revocation are different events and stay separate columns:
    #: "it lapsed as planned" and "we took it away" are not the same answer to an audit.
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.prefix} ({self.use_case.slug})"
