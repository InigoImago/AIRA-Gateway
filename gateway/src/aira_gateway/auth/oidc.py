"""OIDC bearer (JWT) validation for the gateway (FRD-101 Slice B).

Wraps the shared :class:`aira_common.oidc.JwtVerifier` and maps verified claims to a
gateway :class:`Principal` (subject + use-case membership from Keycloak groups).
"""

from __future__ import annotations

from aira_common.oidc import JwtVerifier, SigningKeyResolver, build_jwks_client
from aira_gateway.auth.attribution import usecases_from_groups
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings


class OidcValidator:
    """Validates Keycloak JWTs and resolves them to a gateway Principal."""

    def __init__(
        self,
        issuer: str,
        audience: str | None,
        jwks: SigningKeyResolver,
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self._verifier = JwtVerifier(issuer, audience, jwks, algorithms)

    def validate(self, token: str) -> Principal | None:
        claims = self._verifier.verify(token)
        if claims is None:
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
        jwks=build_jwks_client(settings.jwks_uri()),
    )
