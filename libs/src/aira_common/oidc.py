"""Shared OIDC JWT verification, for the gateway and the management backend.

Verifies a Keycloak JWT against the issuer's JWKS (signature, issuer, expiry, audience) and returns
the claims, or ``None``. The JWKS client is injectable, so callers test without a live Keycloak.

- **An absent claim is not a claim that passed.** PyJWT accepts a token with no `exp` at all, so
  `exp`, `iat` and `sub` are **required**: `sub` is what every audit row and decision is attributed
  to, and `iat` dates the token. Absence of information is not permission.
- **The audience is optional here and required by deployment**: `aira_gateway.security` refuses to
  start outside local development with OIDC on and no audience named.
- **Two clock tolerances (`FRD-134`).** PyJWT's single `leeway` covers `iat`, `nbf` and `exp`. A
  future `iat`/`nbf` means our clock is behind and extends nobody's access; a past `exp` extends a
  credential beyond what the issuer granted. So skew is tolerated and expiry, by default, is not —
  otherwise a verifier one second behind its issuer refuses every fresh token as a `401`.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from aira_common.integration_debug import watch
from aira_common.logging import get_logger

#: How far the issuer's clock may run **ahead** of ours before a token is refused as not yet valid.
#: What most OIDC libraries default to.
DEFAULT_CLOCK_SKEW_SECONDS = 60.0

#: How long past `exp` a token is still accepted. **Zero**: this is the half that extends a
#: credential's life; an installation absorbing a client's broken refresh sets it (`FRD-107` §5.5).
DEFAULT_EXPIRY_LEEWAY_SECONDS = 0.0

#: Above this a tolerance is a second lifetime — Keycloak's access tokens live 300 s at this realm.
MAX_TOLERANCE_SECONDS = 300.0

#: How long the JWKS fetch may take (`FRD-617` §3.4). Five seconds rather than PyJWT's thirty:
#: `PyJWKClient` fetches synchronously, so a Keycloak that accepts and never answers holds the
#: calling thread that long. The key set is cached, so this is paid on a cold start or a rotation.
DEFAULT_JWKS_TIMEOUT_SECONDS = 5.0

_log = get_logger("aira_common.oidc")


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

    @property
    def _jwks_uri(self) -> str:
        """Where the key set is fetched from, for the log line. `""` for an injected fake."""
        return str(getattr(self._jwks, "uri", "") or "")

    def _signing_key(self, token: str) -> Any:
        """Fetch the key this token was signed with, or ``None`` — saying which kind of ``None``.

        A connection error is the **provider** being unavailable and is logged as such, so an
        identity-provider outage is not reported as every user's credential being invalid. Any
        other failure is the **token** — most often no key for its `kid`, which the multi-issuer
        probe in `OidcValidator.validate` relies on to try the next realm.
        """
        try:
            with watch("auth", "jwks.fetch", target=self._jwks_uri, issuer=self._issuer):
                return self._jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            _log.warning(
                "oidc_jwks_unavailable",
                issuer=self._issuer,
                jwks_uri=self._jwks_uri,
                error=str(exc),
                error_type=type(exc).__name__,
                detail=(
                    "The identity provider's key set could not be fetched, so every token is "
                    "being refused as a 401. This is not the callers' credentials."
                ),
            )
            return None
        except jwt.PyJWTError as exc:
            # Not an outage. `PyJWTError` rather than `PyJWKClientError`, because `PyJWKClient`
            # raises `DecodeError` for a malformed token before any fetch — and a caller's own
            # value must never become a `500` (`LESSONS.md` §1).
            _log.info("oidc_token_rejected", reason=type(exc).__name__, detail=str(exc))
            return None

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the verified claims, or None if the token is invalid/expired."""
        signing_key = self._signing_key(token)
        if signing_key is None:
            return None
        try:
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
        # The expiry half, narrowed: `decode` accepted up to `clock_skew` past `exp`, and this
        # refuses what `expiry_leeway` does not cover. Subtractive only, so no verification is
        # reimplemented and `exp` stays required.
        overdue = time.time() - float(claims["exp"])
        if overdue > self._expiry_leeway:
            _log.info(
                "oidc_token_expired",
                seconds_past_expiry=round(overdue, 1),
                expiry_leeway_seconds=self._expiry_leeway,
            )
            return None
        return claims


def build_jwks_client(jwks_uri: str, timeout: float = DEFAULT_JWKS_TIMEOUT_SECONDS) -> PyJWKClient:
    """A caching JWKS client for ``jwks_uri``, with a **bounded** fetch (see
    :data:`DEFAULT_JWKS_TIMEOUT_SECONDS`)."""
    return PyJWKClient(jwks_uri, timeout=timeout)
