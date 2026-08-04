"""Keycloak JWT bearer authentication for DRF (FRD-200).

Verifies the bearer token with the shared :class:`aira_common.oidc.JwtVerifier` and
provisions a Django user from the verified claims. Verified claims are attached as
``request.auth`` for RBAC (FRD-201).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from aira_common.oidc import JwtVerifier, build_jwks_client
from aira_management.config.runtime import get_settings

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
        return self._provision_user(claims), claims

    def authenticate_header(self, request: Request) -> str:
        # Present so DRF returns 401 (not 403) for unauthenticated requests.
        return "Bearer"

    @staticmethod
    def _provision_user(claims: dict[str, Any]) -> AbstractBaseUser:
        user_model = get_user_model()
        subject = str(claims.get("sub"))
        username = claims.get("preferred_username") or subject
        user, _created = user_model.objects.get_or_create(
            username=username, defaults={"email": claims.get("email", "")}
        )
        return user
