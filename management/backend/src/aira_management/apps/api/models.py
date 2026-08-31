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


class PendingIdentity(models.Model):
    """A local account that exists **before** its owner has ever signed in.

    Two things need one, and both are ordinary:

    - the **seed** creates the demo people, so that a walkthrough has somebody to be;
    - an **administrator granting access** names somebody the directory knows and who has not been
      here yet (`FRD-209` FR-4). Refusing that made the console offer a person in the picker and
      the server refuse them — `FRD-206`'s defect, and it was the *only* way the person-grant half
      of `FRD-209` could be used at all.

    Such an account has no `sub` to bind to, so it can only be recognised by **name** on the first
    sign-in. That recognition is exactly what must not be ambient: until this model existed,
    `_provision_user` claimed *any* Django account whose username matched the token's
    `preferred_username` and which was not yet bound — "trust on first use". Measured on
    2026-08-30: a token carrying `preferred_username: "admin"` and an arbitrary `sub` was handed
    the seeded `admin` account, its memberships, its object permissions and (until the same round)
    its `is_superuser` flag. The claim was available to whoever asked first, and nothing recorded
    that it had been made.

    A row here is therefore an **invitation**: created deliberately, visible to whoever created it,
    claimed **once**, and deleted the moment it is. An account with no row is not claimable by
    anybody — which is every account that has already signed in, and every account nobody invited.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_identity",
        primary_key=True,
    )
    #: Who invited them — the same fact `UseCaseGroupGrant.granted_by` keeps, for the same reason:
    #: a review asks who opened a door. Blank for the seed, which is not a person.
    invited_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"pending {self.user}"
