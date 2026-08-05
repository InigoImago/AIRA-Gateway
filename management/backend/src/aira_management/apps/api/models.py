"""Identity mapping between Keycloak subjects and Django users (ADR-0007).

Keycloak's ``sub`` is the only stable identifier for a person: usernames can be renamed, and a
username freed by a departing employee can be handed to someone else. Keying the Django user on
``preferred_username`` would hand that new person the previous holder's object-level
permissions and use-case memberships, so the binding is recorded here instead.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class OidcIdentity(models.Model):
    """The Keycloak ``sub`` a Django user was provisioned from."""

    subject = models.CharField(max_length=255, unique=True, db_index=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oidc_identity"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.subject} -> {self.user}"
