"""OIDC bearer (JWT) validation against Keycloak (FRD-101 Slice B).

Validates the JWT signature via the issuer's JWKS, plus issuer, expiry, and (optionally)
audience. Resolves a valid token to a Principal. The JWKS client is injectable so the
validator is unit-testable without a live Keycloak.
"""

from __future__ import annotations

from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from aira_gateway.auth.attribution import usecases_from_groups
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings


class SigningKey(Protocol):
    key: Any


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class OidcValidator:
    """Validates Keycloak JWTs and resolves them to a Principal."""

    def __init__(
        self,
        issuer: str,
        audience: str | None,
        jwks: SigningKeyResolver,
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self._issuer = issuer
        self._audience = audience or None
        self._jwks = jwks
        self._algorithms = list(algorithms)

    def validate(self, token: str) -> Principal | None:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                options={"verify_aud": self._audience is not None},
            )
        except jwt.PyJWTError:
            return None
        subject = claims.get("sub")
        if not subject:
            return None
        raw_groups = claims.get("groups")
        groups = raw_groups if isinstance(raw_groups, list) else []
        return Principal(
            subject=str(subject), method="oidc", use_cases=usecases_from_groups(groups)
        )


def build_oidc_validator(settings: GatewaySettings) -> OidcValidator | None:
    """Build an OidcValidator from settings, or None when OIDC is disabled/unconfigured."""
    if not settings.oidc_enabled or not settings.oidc_issuer:
        return None
    return OidcValidator(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks=PyJWKClient(settings.jwks_uri()),
    )
