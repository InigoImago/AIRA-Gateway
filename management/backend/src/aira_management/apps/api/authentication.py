"""Keycloak JWT bearer authentication for DRF (FRD-200).

Verifies the bearer token with the shared :class:`aira_common.oidc.JwtVerifier` and
provisions a Django user from the verified claims. Verified claims are attached as
``request.auth`` for RBAC (FRD-201).

Users are bound to the token's ``sub``, not to its ``preferred_username`` — see
:mod:`aira_management.apps.api.models` for why (ADR-0007).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from aira_common.oidc import JwtVerifier, build_jwks_client
from aira_management.apps.api.models import OidcIdentity
from aira_management.config.runtime import get_settings
from aira_management.rbac import sync_user_roles

_BEARER = "bearer "


@lru_cache(maxsize=1)
def build_management_verifier() -> JwtVerifier | None:
    """Build the JWT verifier from settings, or None when OIDC is unconfigured."""
    settings = get_settings()
    if not settings.oidc_issuer:
        return None
    return JwtVerifier(
        settings.oidc_issuer, settings.oidc_audience, build_jwks_client(settings.jwks_uri())
    )


class KeycloakJWTAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[AbstractBaseUser, dict[str, Any]] | None:
        header = request.headers.get("Authorization", "")
        if header[: len(_BEARER)].lower() != _BEARER:
            return None

        verifier = build_management_verifier()
        if verifier is None:
            return None

        claims = verifier.verify(header[len(_BEARER) :].strip())
        if claims is None:
            raise AuthenticationFailed("Invalid or expired token.")
        subject = claims.get("sub")
        if not subject:
            raise AuthenticationFailed("Token has no subject.")
        user = self._provision_user(str(subject), claims)
        sync_user_roles(user, claims)  # Keycloak realm roles are the source of truth
        return user, claims

    def authenticate_header(self, request: Request) -> str:
        # Present so DRF returns 401 (not 403) for unauthenticated requests.
        return "Bearer"

    @staticmethod
    @transaction.atomic
    def _provision_user(subject: str, claims: dict[str, Any]) -> AbstractBaseUser:
        """Resolve the Django user for ``subject``, creating one on first sight.

        Resolution order:
        1. an existing ``OidcIdentity`` for this ``sub`` — the authoritative binding;
        2. otherwise a user with the token's ``preferred_username`` that is *not yet bound* to
           another subject (trust on first use, so accounts that predate this binding keep
           their permissions), which then gets bound;
        3. otherwise a fresh user, whose username is suffixed if the preferred one is taken by
           somebody else's identity.
        """
        identity = OidcIdentity.objects.filter(subject=subject).select_related("user").first()
        if identity is not None:
            return identity.user

        user_model = get_user_model()
        preferred = str(claims.get("preferred_username") or subject)
        email = claims.get("email", "")

        existing = user_model.objects.filter(username=preferred).first()
        if existing is not None and not OidcIdentity.objects.filter(user=existing).exists():
            OidcIdentity.objects.create(subject=subject, user=existing)
            return existing

        username = preferred if existing is None else f"{preferred}-{subject[:8]}"
        user = user_model.objects.create(username=username, email=email)
        OidcIdentity.objects.create(subject=subject, user=user)
        return user
