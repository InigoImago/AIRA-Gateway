"""Identity mapping between Keycloak subjects and Django users (`ADR-0007`).

Keycloak's ``sub`` is the only stable identifier for a person: usernames can be renamed, and one
freed by a departing employee can be handed to somebody else. Keying the Django user on
``preferred_username`` would hand that person the previous holder's permissions and memberships,
so the binding is recorded here instead.
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


class PendingIdentity(models.Model):
    """An **invitation**: a local account that exists before its owner has ever signed in.

    The seed creates one for each demo person, and an administrator granting access to somebody
    the directory knows creates one too (`FRD-209` FR-4). Having no `sub` to bind to, such an
    account can only be recognised by name on the first sign-in — so that recognition must not be
    ambient. A row here is created deliberately, claimed **once** and deleted when claimed; an
    account without one is claimable by nobody.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_identity",
        primary_key=True,
    )
    #: Who invited them: a review asks who opened a door. Blank for the seed, which is not a person.
    invited_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"pending {self.user}"
