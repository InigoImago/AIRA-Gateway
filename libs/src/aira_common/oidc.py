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

**Two tolerances, because they cost different things (`FRD-134`).** PyJWT applies one `leeway` to
`iat`, `nbf` and `exp`, and those are not one question. `iat`/`nbf` in the future means *our* clock
is behind the issuer's, and accepting it extends nobody's access — the token was genuinely minted.
`exp` in the past extends a credential's life beyond what the issuer granted. Collapsing them means
an installation that only wants the first has to buy the second, and the first is the one that takes
a service down: because `exp`, `iat` and `sub` are all required above, a verifier one second behind
its issuer refuses **every** freshly minted token as *not yet valid* — answered as `401` and
indistinguishable from a wrong secret.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from aira_common.logging import get_logger

_log = get_logger("aira_common.oidc")

#: How far the issuer's clock may run **ahead** of this host's before a token is refused as not yet
#: valid. What the predecessor's contract grants, and what most OIDC libraries default to.
DEFAULT_CLOCK_SKEW_SECONDS = 60.0

#: How long past `exp` a token is still accepted. **Zero**, deliberately: this is the half that
#: extends a credential's life. The predecessor grants 60 seconds here too; an installation that
#: wants to absorb a client's broken refresh strategy sets the value (`FRD-107` §5.5).
DEFAULT_EXPIRY_LEEWAY_SECONDS = 0.0

#: A tolerance above this is not skew tolerance, it is a second lifetime — Keycloak's own access
#: tokens live 300 seconds at this realm.
MAX_TOLERANCE_SECONDS = 300.0


class ToleranceOutOfRange(ValueError):
    """A configured clock tolerance that would be a lifetime rather than a tolerance."""


def check_tolerance(value: float, name: str) -> float:
    """Refuse a tolerance at construction, which is startup, rather than per request."""
    if value < 0 or value > MAX_TOLERANCE_SECONDS:
        raise ToleranceOutOfRange(
            f"{name} is {value}; it must be between 0 and {MAX_TOLERANCE_SECONDS:.0f} seconds. "
            "Above that it stops being tolerance for a clock and becomes a second lifetime for "
            "every token."
        )
    return float(value)


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
        clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS,
        expiry_leeway_seconds: float = DEFAULT_EXPIRY_LEEWAY_SECONDS,
    ) -> None:
        self._issuer = issuer
        self._audience = audience or None
        self._jwks = jwks
        self._algorithms = list(algorithms)
        self._clock_skew = check_tolerance(clock_skew_seconds, "AIRA_OIDC_CLOCK_SKEW_SECONDS")
        self._expiry_leeway = check_tolerance(
            expiry_leeway_seconds, "AIRA_OIDC_EXPIRY_LEEWAY_SECONDS"
        )

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
                # Covers `iat`, `nbf` **and** `exp`; the expiry half is narrowed again below.
                leeway=self._clock_skew,
                options={
                    "verify_aud": self._audience is not None,
                    # Present *and* valid. Without this, a token with no `exp` verifies happily.
                    "require": ["exp", "iat", "sub"],
                },
            )
        except jwt.PyJWTError as exc:
            _log.info(
                "oidc_token_rejected",
                reason=type(exc).__name__,
                clock_skew_seconds=self._clock_skew,
                expiry_leeway_seconds=self._expiry_leeway,
                detail=(
                    "A token refused as not-yet-valid usually means this host's clock is behind "
                    "the issuer's; raise AIRA_OIDC_CLOCK_SKEW_SECONDS or fix the clock."
                    if isinstance(exc, jwt.ImmatureSignatureError)
                    else str(exc)
                ),
            )
            return None
        # **The expiry half, narrowed.** `decode` accepted anything up to `clock_skew` seconds past
        # `exp`; this refuses what `expiry_leeway` does not cover. Subtractive only — it can reject
        # what `decode` allowed and never the reverse — so no verification is reimplemented here and
        # `exp` stays a required claim. At the defaults the behaviour for `exp` is exactly what it
        # was before `FRD-134`, and only a clock that is *behind* became forgiving.
        overdue = time.time() - float(claims["exp"])
        if overdue > self._expiry_leeway:
            _log.info(
                "oidc_token_expired",
                seconds_past_expiry=round(overdue, 1),
                expiry_leeway_seconds=self._expiry_leeway,
            )
            return None
        return claims


def build_jwks_client(jwks_uri: str) -> PyJWKClient:
    """Build a caching JWKS client for the given URI."""
    return PyJWKClient(jwks_uri)
