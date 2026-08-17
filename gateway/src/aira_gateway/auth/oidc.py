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


class OidcValidator:
    """Validates Keycloak JWTs and resolves them to a gateway Principal.

    **One realm or several** (`FRD-118` FR-1). A deployment usually has one; an organisation
    migrating between realms, or running a second instance, has two for as long as the move takes,
    and a gateway that accepts only one of them makes the migration a flag day for every client.

    Routing is by the token's **own `iss` claim, read unverified** — a hint, never a trust
    decision: the verifier it selects then checks `iss` for real, against the value it was
    configured with, so a forged `iss` selects a verifier that refuses it. Where no configured
    issuer matches, every verifier is tried in turn, which is the `kid` probe the predecessor
    describes: each one asks its own JWKS for the key id and refuses a key it does not have.

    Routing by `iss` first matters for more than speed. A probe makes each JWKS client refresh on a
    key it will never hold, so a token from realm B would make realm A refetch its key set on every
    single request — a remote call per request, added by a feature meant to be invisible.
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
        # An absent mapping grants no roles, which is the safe reading and the one an installation
        # that has not configured `AIRA_ROLE_GROUPS` gets: oversight is withheld, never assumed.
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
                # It named this realm and this realm refused it. Trying the others would only
                # produce the same refusal from every one of them, and each costs a JWKS refresh
                # for a key id none of them will ever hold.
                return None
        return None

    def _principal(self, claims: dict[str, Any], issuer: str) -> Principal | None:
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
            #: Which realm minted this token. Carried so an audit row answers "who issued the
            #: credential this decision was made on" during a migration, when the answer is
            #: genuinely two different systems.
            issuer=issuer,
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
    if not settings.oidc_enabled or not settings.issuers():
        return None
    if any(not audience for _, audience, _ in settings.issuers()):
        # Without an audience, *any* token the realm issued — including one minted for an
        # unrelated client — is accepted here. Fine locally, a real weakness in production.
        get_logger("aira_gateway").warning(
            "oidc_audience_unset",
            issuer=", ".join(name for name, audience, _ in settings.issuers() if not audience),
            detail="Set AIRA_OIDC_AUDIENCE so tokens issued for other clients are rejected.",
        )
    configured = settings.issuers()
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
