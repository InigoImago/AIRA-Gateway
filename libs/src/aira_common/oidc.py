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

from aira_common.integration_debug import watch
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

#: How long the JWKS fetch may take before it is abandoned (`FRD-617` §3.4).
#:
#: **Five seconds, against PyJWT's default of thirty.** `PyJWKClient` fetches with `urllib`, which
#: is synchronous, and `resolve_principal` calls `validate` from an `async` dependency — so before
#: this the gateway ran that fetch on its event loop with a thirty-second ceiling. A Keycloak that
#: accepts connections and does not answer therefore stalled *every* concurrent request on the
#: worker, including the ones authenticating with an API key and the ones asking `/readyz`. The
#: thread in `resolve_principal` is the other half of the fix; this is the bound on how long a
#: thread can be held.
#:
#: The key set is cached, so this is paid on a cold start and on a key rotation, not per request.
DEFAULT_JWKS_TIMEOUT_SECONDS = 5.0


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
        """Fetch the key this token was signed with, or None — **saying which kind of None**.

        `PyJWKClientError` is a subclass of `PyJWTError`, so before `FRD-617` this call sat inside
        the same `try` as the decode below and a refused connection, a DNS failure and a read
        timeout against the identity provider all came out as `oidc_token_rejected` at `INFO`, and
        as a `401`. That is the moment Keycloak goes away being reported to every operator as
        *every user's credential is suddenly invalid* — a sentence that sends whoever reads it to
        the wrong system entirely.

        The split is by *which* `PyJWKClientError`: a connection error is the provider, and any
        other one — most often "no key matching this `kid`" — is the token, which is also what the
        multi-issuer probe in `OidcValidator.validate` depends on to move on to the next realm.
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
            # Not an outage. Two shapes reach here and both are the *token*: a key set that was
            # read and holds no key for this `kid` (`PyJWKClientError`), and a token `PyJWKClient`
            # could not even parse to find the `kid` in — `DecodeError`, which it raises before
            # any fetch happens.
            #
            # **The second is why this catches `PyJWTError` and not `PyJWKClientError`.** Splitting
            # the fetch out of `verify` narrowed what the fetch's own `except` covered, and a
            # malformed bearer token — a truncated header, a stray character, anything a client can
            # send — then propagated out of `verify()` as an unhandled exception: a `500` where the
            # answer had always been `401`. Found by pointing the demonstration at a hand-written
            # token, which is exactly the value a caller sends. `LESSONS.md` §1: a caller's own
            # value must never become a server error.
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


def build_jwks_client(jwks_uri: str, timeout: float = DEFAULT_JWKS_TIMEOUT_SECONDS) -> PyJWKClient:
    """Build a caching JWKS client for the given URI, with a **bounded** fetch.

    The timeout was PyJWT's default of thirty seconds because nothing was passed — see
    :data:`DEFAULT_JWKS_TIMEOUT_SECONDS` for what that cost on an async request path.
    """
    return PyJWKClient(jwks_uri, timeout=timeout)
