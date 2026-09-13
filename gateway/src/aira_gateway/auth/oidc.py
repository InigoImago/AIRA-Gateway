"""OIDC bearer (JWT) validation for the gateway (`FRD-101`).

Wraps the shared :class:`aira_common.oidc.JwtVerifier` and maps verified claims to a gateway
:class:`Principal`. Roles and use-case membership both come from the `groups` claim (`ADR-0017`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import jwt

from aira_common.access import usecases_from_group_paths
from aira_common.logging import get_logger
from aira_common.oidc import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    DEFAULT_EXPIRY_LEEWAY_SECONDS,
    JwtVerifier,
    SigningKeyResolver,
    build_jwks_client,
)
from aira_common.roles import Role, roles_from_groups
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

#: Bounds on claims that reach stored fields.
_MAX_USERNAME = 150
_MAX_CLIENT = 64


class OidcValidator:
    """Validates Keycloak JWTs and resolves them to a gateway Principal.

    **One realm or several** (`FRD-118` FR-1), so a migration between realms is not a flag day for
    every client. Routing is by the token's **own `iss` claim, read unverified** — a hint, never a
    trust decision: the selected verifier checks `iss` for real, so a forged `iss` selects a
    verifier that refuses it. Where no issuer matches, each verifier is tried in turn (the `kid`
    probe). Routing first matters: a probe makes each JWKS client refresh on a key it will never
    hold, a remote call per request.
    """

    def __init__(
        self,
        issuer: str,
        audience: str | None,
        jwks: SigningKeyResolver,
        algorithms: tuple[str, ...] = ("RS256",),
        role_groups: dict[Role, tuple[str, ...]] | None = None,
        clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS,
        expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS,
        others: Sequence[tuple[str, str, SigningKeyResolver]] = (),
    ) -> None:
        self._verifiers: tuple[tuple[str, JwtVerifier], ...] = tuple(
            (
                name,
                JwtVerifier(name, aud, keys, algorithms, clock_skew_seconds, expiry_leeway_seconds),
            )
            for name, aud, keys in ((issuer, audience or "", jwks), *others)
        )
        # An absent mapping grants no roles: oversight is withheld, never assumed.
        self._role_groups = role_groups or {}

    def _claimed_issuer(self, token: str) -> str | None:
        """The `iss` of an **unverified** token, for routing only."""
        try:
            return (
                str(jwt.decode(token, options={"verify_signature": False}).get("iss") or "") or None
            )
        except jwt.PyJWTError:
            return None

    def validate(self, token: str) -> Principal | None:
        claimed = self._claimed_issuer(token)
        ordered = sorted(self._verifiers, key=lambda pair: pair[0] != claimed)
        for issuer, verifier in ordered:
            claims = verifier.verify(token)
            if claims is not None:
                return self._principal(claims, issuer)
            if claimed is not None and issuer == claimed:
                # The realm it named refused it; the others would refuse it too, each after a JWKS
                # refresh for a key id they will never hold.
                return None
        return None

    def _principal(self, claims: dict[str, Any], issuer: str) -> Principal | None:
        subject = claims.get("sub")
        if not subject:
            return None
        raw_groups = claims.get("groups")
        # Filtered once, here, for every reader: a group emitted as an object must cost that caller
        # a role, not raise inside token validation.
        groups = (
            [path for path in raw_groups if isinstance(path, str)]
            if isinstance(raw_groups, list)
            else []
        )
        # `azp` (authorized party) or, on client-credentials tokens, `client_id`: *which system*,
        # so one person's tokens from two applications differ in the audit trail.
        client = claims.get("azp") or claims.get("client_id")
        # The name, carried **beside** `sub` and never instead of it. An absent or blank claim is
        # no name, not an empty one.
        name = claims.get("preferred_username")
        username = str(name)[:_MAX_USERNAME] if isinstance(name, str) and name.strip() else None
        return Principal(
            subject=str(subject),
            method="oidc",
            issuer=issuer,
            username=username,
            credential=str(client)[:_MAX_CLIENT] if client else None,
            # The `/use-cases/<slug>` convention, resolvable from the token alone (`FRD-102`).
            # Group grants are added a layer out (`auth/grants.py`) — a union, so this route keeps
            # working when the read-model cannot be read.
            use_cases=usecases_from_group_paths(groups),
            groups=tuple(groups),
            roles=roles_from_groups(groups, self._role_groups),
        )


def build_oidc_validator(settings: GatewaySettings) -> OidcValidator | None:
    """Build an OidcValidator from settings, or None when OIDC is disabled/unconfigured."""
    if not settings.oidc_enabled:
        return None
    configured = settings.issuers()
    if not configured:
        return None
    if any(not audience for _, audience, _ in configured):
        # Without an audience, *any* token the realm issued — including one minted for an
        # unrelated client — is accepted. Fine locally, a real weakness in production.
        get_logger("aira_gateway").warning(
            "oidc_audience_unset",
            issuer=", ".join(name for name, audience, _ in configured if not audience),
            detail="Set AIRA_OIDC_AUDIENCE so tokens issued for other clients are rejected.",
        )
    first, *rest = configured
    return OidcValidator(
        issuer=first[0],
        audience=first[1],
        jwks=build_jwks_client(first[2]),
        role_groups=settings.parsed_role_groups(),
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        expiry_leeway_seconds=settings.oidc_expiry_leeway_seconds,
        others=tuple((name, aud, build_jwks_client(uri)) for name, aud, uri in rest),
    )
