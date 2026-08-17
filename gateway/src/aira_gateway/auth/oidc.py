"""OIDC bearer (JWT) validation for the gateway (FRD-101 Slice B).

Wraps the shared :class:`aira_common.oidc.JwtVerifier` and maps verified claims to a
gateway :class:`Principal` (subject + use-case membership from Keycloak groups).

**Roles come from groups (`ADR-0017`).** Until 2026-08-09 this read `realm_access.roles` while
use-case membership came from the `groups` claim — two mechanisms answering "who is this". The
gateway's whole role vocabulary is `is_governance`, `is_oversight` and `may_act_on_incidents`,
all built from three organisation-wide roles, so the change is exactly this one call: the two
use-case roles were never read here at all.
"""

from __future__ import annotations

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


class OidcValidator:
    """Validates Keycloak JWTs and resolves them to a gateway Principal."""

    def __init__(
        self,
        issuer: str,
        audience: str | None,
        jwks: SigningKeyResolver,
        algorithms: tuple[str, ...] = ("RS256",),
        role_groups: dict[Role, tuple[str, ...]] | None = None,
        clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS,
        expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS,
    ) -> None:
        self._verifier = JwtVerifier(
            issuer, audience, jwks, algorithms, clock_skew_seconds, expiry_leeway_seconds
        )
        # An absent mapping grants no roles, which is the safe reading and the one an installation
        # that has not configured `AIRA_ROLE_GROUPS` gets: oversight is withheld, never assumed.
        self._role_groups = role_groups or {}

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
        # The name a person is known by, carried **beside** `sub` and never instead of it: a
        # username can be reassigned to somebody else, so keying anything on it would move one
        # person's history onto another. Bounded like every other claim that reaches a stored
        # field, and taken only when it is a non-empty string — an absent claim is not an empty
        # name, it is no name.
        name = claims.get("preferred_username")
        username = str(name)[:150] if isinstance(name, str) and name.strip() else None
        return Principal(
            subject=str(subject),
            method="oidc",
            username=username,
            credential=str(client)[:64] if client else None,
            # The `/use-cases/<slug>` convention, resolvable from the token alone (`FRD-102`).
            # Group *grants* are added a layer out, where the read-model is — see
            # `auth/grants.py`. Union, not replacement: this route keeps working, including when
            # the read-model cannot be read.
            use_cases=usecases_from_group_paths(groups),
            groups=tuple(str(group) for group in groups if isinstance(group, str)),
            roles=roles_from_groups(
                (str(group) for group in groups if isinstance(group, str)), self._role_groups
            ),
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
        role_groups=settings.parsed_role_groups(),
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        expiry_leeway_seconds=settings.oidc_expiry_leeway_seconds,
    )
