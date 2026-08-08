"""Shared OIDC JWT verification (used by the gateway and the management backend).

Verifies a Keycloak JWT against the issuer's JWKS (signature, issuer, expiry, audience) and
returns the claims, or None if invalid. The JWKS client is injectable so callers can unit-test
without a live Keycloak.

**A claim that is absent is not a claim that passed.** PyJWT verifies `exp` when it is present
and accepts a token that carries none at all — so a token minted without one, or with the claim
stripped by anything between the issuer and here, was a credential that never expired. `sub` is
the subject every audit row, every membership decision and every budget booking is attributed to,
and `iat` is what makes "this token is older than the incident" answerable. All three are now
**required**, which is the same rule this project keeps arriving at from different directions:
absence of information is not permission.

The audience stays optional *here* and is required by deployment: `aira_gateway.security` refuses
to start outside local development with OIDC on and no audience named. Putting it there rather
than in the verifier keeps a laptop working against a realm that has no audience mapper, while
making the production case impossible to reach by accident.
"""

from __future__ import annotations

from typing import Any, Protocol

import jwt
from jwt import PyJWKClient


class SigningKey(Protocol):
    key: Any


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class JwtVerifier:
    """Verifies Keycloak JWTs and returns their claims."""

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

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the verified claims, or None if the token is invalid/expired."""
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "verify_aud": self._audience is not None,
                    # Present *and* valid. Without this, a token with no `exp` verifies happily.
                    "require": ["exp", "iat", "sub"],
                },
            )
        except jwt.PyJWTError:
            return None
        return claims


def build_jwks_client(jwks_uri: str) -> PyJWKClient:
    """Build a caching JWKS client for the given URI."""
    return PyJWKClient(jwks_uri)
