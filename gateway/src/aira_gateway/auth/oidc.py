"""OIDC bearer (JWT) validation for the gateway (FRD-101 Slice B).

Wraps the shared :class:`aira_common.oidc.JwtVerifier` and maps verified claims to a
gateway :class:`Principal` (subject + use-case membership from Keycloak groups).
"""

from __future__ import annotations

from aira_common.logging import get_logger
from aira_common.oidc import JwtVerifier, SigningKeyResolver, build_jwks_client
from aira_gateway.auth.attribution import realm_roles, usecases_from_groups
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
        # `azp` (authorized party) is the client the token was issued to; `client_id` appears on
        # client-credentials tokens. Either answers "which system", which is a different question
        # from `sub` — the same person's token from two applications should not look identical in
        # the audit trail.
        client = claims.get("azp") or claims.get("client_id")
        return Principal(
            subject=str(subject),
            method="oidc",
            credential=str(client)[:64] if client else None,
            # The `/use-cases/<slug>` convention, resolvable from the token alone (`FRD-102`).
            # Group *grants* are added a layer out, where the read-model is — see
            # `auth/grants.py`. Union, not replacement: this route keeps working, including when
            # the read-model cannot be read.
            use_cases=usecases_from_groups(groups),
            groups=tuple(str(group) for group in groups if isinstance(group, str)),
            roles=realm_roles(claims),
        )


def build_oidc_validator(settings: GatewaySettings) -> OidcValidator | None:
    """Build an OidcValidator from settings, or None when OIDC is disabled/unconfigured."""
    if not settings.oidc_enabled or not settings.oidc_issuer:
        return None
    if not settings.oidc_audience:
        # Without an audience, *any* token the realm issued — including one minted for an
        # unrelated client — is accepted here. Fine locally, a real weakness in production.
        get_logger("aira_gateway").warning(
            "oidc_audience_unset",
            issuer=settings.oidc_issuer,
            detail="Set AIRA_OIDC_AUDIENCE so tokens issued for other clients are rejected.",
        )
    return OidcValidator(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks=build_jwks_client(settings.jwks_uri()),
    )
