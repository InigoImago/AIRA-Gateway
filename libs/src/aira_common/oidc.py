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

import http.client
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import jwt
from jwt import PyJWK, PyJWKClient, PyJWKSet
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

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

#: How often a token naming a key the cached set does not hold may make the set be fetched again.
#: A rotation publishes its new key before signing with it, so one refetch per interval finds it;
#: without the bound every forged `kid` is a synchronous request to the identity provider.
JWKS_REFRESH_INTERVAL_SECONDS = 30.0

#: How long a fetched key set is used before it is fetched again: PyJWT's own default, named here
#: because the rules below are measured against it.
JWKS_LIFESPAN_SECONDS = 300.0

#: How long the last key set fetched keeps verifying tokens while a new one **cannot be fetched** —
#: the provider refusing, hanging, or answering something that is not a key set. Without it every
#: token is refused the moment the set expires during an outage. Bounded, because a key the provider
#: withdraws meanwhile — a compromised one — is still accepted until then: an hour outlasts a
#: restart or a maintenance window and keeps that exposure finite. A set the provider *answers* is
#: adopted at once, whatever it holds.
JWKS_STALE_LIMIT_SECONDS = 3600.0

#: After a failed fetch the next one waits this long, doubling with every further failure up to
#: :data:`JWKS_RETRY_MAX_SECONDS`. Meanwhile a request is answered from the set held, or refused at
#: once where none is — rather than each one asking the provider again and waiting out the timeout.
JWKS_RETRY_FIRST_SECONDS = 1.0
JWKS_RETRY_MAX_SECONDS = 30.0

#: The longest bearer token parsed at all: twice what the shipped proxy forwards in one header
#: line (nginx's 8 KB default). Anything longer is refused unread, because parsing is where a
#: caller's value turns into work — and, nested deeply enough, into an exception no parser wraps.
MAX_TOKEN_LENGTH = 16 * 1024

_log = get_logger("aira_common.oidc")


class ToleranceOutOfRange(ValueError):
    """A configured clock tolerance that would be a lifetime rather than a tolerance."""


def check_tolerance(value: float, name: str) -> float:
    """Refuse a tolerance at construction, which is startup, rather than per request.

    A finite number first: NaN fails every comparison, so it passed a range check and made every
    comparison against `exp` false — an expired token verified.
    """
    if not math.isfinite(value) or value < 0 or value > MAX_TOLERANCE_SECONDS:
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

        A key set that cannot be fetched or read is the **provider** being unavailable and is
        logged as such, so an identity-provider outage is not reported as every user's credential
        being invalid. Any other failure is the **token** — most often no key for its `kid`.
        """
        try:
            with watch("auth", "jwks.fetch", target=self._jwks_uri, issuer=self._issuer):
                return self._jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            self._provider_unavailable(exc)
            return None
        except jwt.PyJWTError as exc:
            # Not an outage. `PyJWTError` rather than `PyJWKClientError`, because `PyJWKClient`
            # raises `DecodeError` for a malformed token before any fetch — and a caller's own
            # value must never become a `500` (`LESSONS.md` §1).
            _log.info("oidc_token_rejected", reason=type(exc).__name__, detail=str(exc))
            return None
        except RecursionError as exc:
            # The token's own header, nested deeper than the parser follows: still the caller's
            # value, which PyJWT does not wrap because it is not a `ValueError`.
            _log.info("oidc_token_rejected", reason=type(exc).__name__)
            return None
        except (ValueError, OSError, http.client.HTTPException) as exc:
            # What `PyJWKClient` converts nothing of: a key set that is not JSON (a proxy's
            # sign-in page answered 200), a body cut off mid-read, a connection reset. The token's
            # own parse errors were caught above as `PyJWTError`, so these are the provider.
            self._provider_unavailable(exc)
            return None

    def _provider_unavailable(self, exc: Exception) -> None:
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

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the verified claims, or None if the token is invalid/expired."""
        if len(token) > MAX_TOKEN_LENGTH:
            _log.info("oidc_token_rejected", reason="TokenTooLong", length=len(token))
            return None
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
                # One leeway for `iat`, `nbf` **and** `exp`, so the wider of the two: each claim is
                # narrowed to its own tolerance below, and only narrowed.
                leeway=max(self._clock_skew, self._expiry_leeway),
                options={
                    "verify_aud": self._audience is not None,
                    # Present *and* valid. Without this, a token with no `exp` verifies happily.
                    "require": ["exp", "iat", "sub"],
                },
            )
        except (jwt.PyJWTError, ValueError, RecursionError) as exc:
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
        # Each claim narrowed to its own tolerance (`FRD-134` FR-1): `decode` accepted the wider of
        # the two everywhere. Subtractive only, so no verification is reimplemented and every
        # claim `decode` checked keeps its type check and its presence requirement.
        now = time.time()
        overdue = now - float(claims["exp"])
        if overdue > self._expiry_leeway:
            _log.info(
                "oidc_token_expired",
                seconds_past_expiry=round(overdue, 1),
                expiry_leeway_seconds=self._expiry_leeway,
            )
            return None
        for claim in ("iat", "nbf"):
            ahead = float(claims[claim]) - now if claim in claims else 0.0
            if ahead > self._clock_skew:
                _log.info(
                    "oidc_token_rejected",
                    reason="ImmatureSignatureError",
                    detail=f"{claim} {ahead:.0f}s ahead of this host, tolerance "
                    f"{self._clock_skew:.0f}s",
                )
                return None
        return claims


@dataclass
class _Fetch:
    """The one fetch of the key set in flight. A caller with no set to answer from waits for it —
    no longer than what is left of its timeout — rather than starting another."""

    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    keys: PyJWKSet | None = None


class BoundedRefreshJwkClient(PyJWKClient):
    """A `PyJWKClient` for an identity provider that may hang, may be down, and may be probed.

    - **One fetch at a time, and nobody queues behind it.** PyJWT holds a lock across the fetch, so
      once the cached set expired during an outage each validation fetched in turn and the *n*-th
      waited *n* timeouts — on threads other work needed too. Here one caller fetches while the
      others are answered from the set held; only a caller with no set at all waits, for that one
      fetch and never past its timeout.
    - **The last set fetched outlives a failed fetch** by :data:`JWKS_STALE_LIMIT_SECONDS`, and the
      fetch is retried after a backoff rather than by every request. A set the provider answers
      replaces it at once, whatever it holds — a rotation or a withdrawn key included.
    - **An unknown `kid` fetches the set again at most once per**
      :data:`JWKS_REFRESH_INTERVAL_SECONDS`: the right answer to a rotation, and otherwise a request
      to the provider per forged token. Between refetches it is refused.

    A `kid` the set held does not name is refused, whichever set that is: keeping a set changes how
    long a key is known, never what a known key proves.
    """

    def __init__(
        self,
        uri: str,
        *,
        timeout: float,
        refresh_interval: float = JWKS_REFRESH_INTERVAL_SECONDS,
        lifespan: float = JWKS_LIFESPAN_SECONDS,
        stale_limit: float = JWKS_STALE_LIMIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # PyJWT's cache, its refetch cooldown and the lock it holds across a fetch all stay unused:
        # the rules above replace them, on one clock a test can move.
        super().__init__(uri, timeout=timeout, cache_jwk_set=False)
        self._refresh_interval = refresh_interval
        self._lifespan = lifespan
        self._stale_limit = stale_limit
        self._clock = clock
        # Guards the fields below, and is held only to read or change them — never across a fetch.
        self._state = threading.Lock()
        self._keys: PyJWKSet | None = None
        self._fetched_at = 0.0
        self._fetch: _Fetch | None = None
        self._backoff = 0.0
        self._retry_at = -math.inf
        self._refreshed_at: float | None = None

    def get_signing_key(self, kid: str) -> PyJWK:
        """The key ``kid`` names in the set held, which is fetched again for a name it lacks — at
        most once per refresh interval."""
        key = self.match_kid(self.get_signing_keys(), kid)
        if key is None and self._may_refetch():
            key = self.match_kid(self.get_signing_keys(refresh=True), kid)
        if key is None:
            raise PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return key

    def _may_refetch(self) -> bool:
        """Whether an unknown `kid` may have the set fetched now, spending the interval's refetch.
        Shared by every thread, because a bound kept per thread bounds nothing."""
        with self._state:
            now = self._clock()
            if self._refreshed_at is not None and now - self._refreshed_at < self._refresh_interval:
                return False
            self._refreshed_at = now
            return True

    def get_jwk_set(self, refresh: bool = False) -> PyJWKSet:
        """The set to verify against: the one held while it is fresh, the provider's otherwise.

        ``refresh`` asks for the provider's set although the one held is fresh — for a `kid` it does
        not name.
        """
        with self._state:
            now = self._clock()
            held = self._held(now)
            if held is not None and not refresh and now - self._fetched_at < self._lifespan:
                return held
            fetch = self._fetch
            if fetch is None and now < self._retry_at:
                # A fetch failed lately. Until the backoff has passed, the set held answers — or,
                # with none, the refusal does, at once.
                if held is not None:
                    return held
                raise PyJWKClientConnectionError(
                    "The key set could not be fetched; the next attempt is in "
                    f"{self._retry_at - now:.1f}s."
                )
            if fetch is not None and held is not None and not refresh:
                # Somebody is fetching already, and the set held answers meanwhile.
                return held
            leads = fetch is None
            if fetch is None:
                fetch = self._fetch = _Fetch(deadline=now + self.timeout)
        if leads:
            return self._fetch_for(fetch)
        return self._wait_for(fetch)

    def _held(self, now: float) -> PyJWKSet | None:
        """The set last fetched, while it is younger than the stale limit."""
        if self._keys is None or now - self._fetched_at >= self._stale_limit:
            return None
        return self._keys

    def _fetch_for(self, fetch: _Fetch) -> PyJWKSet:
        """Fetch the set for this caller and for everyone waiting on ``fetch``.

        A fetch that brings no answer — refused, timed out, a page that is not a key set — keeps the
        set held, and a caller holding none is told why. An answer is adopted, even one holding no
        key anything can verify with: that is the provider's word, and the set held stops counting.
        """
        try:
            answered = self.fetch_data()
        except Exception as exc:  # noqa: BLE001 - whatever stopped the fetch, the set held stands
            held = self._settle(fetch, None, dropped=False)
            if held is None:
                raise
            self._report_kept(exc)
            return held
        try:
            keys = PyJWKSet.from_dict(answered)
        except Exception:
            # Answered, with no key that can verify anything: adopted, so nothing verifies.
            self._settle(fetch, None, dropped=True)
            raise
        self._settle(fetch, keys, dropped=False)
        return keys

    def _settle(self, fetch: _Fetch, keys: PyJWKSet | None, *, dropped: bool) -> PyJWKSet | None:
        """Record how ``fetch`` ended, release whoever waits on it, and return the set now held.

        Settled on every path, or the next caller would find a fetch in flight that never ends.
        """
        with self._state:
            now = self._clock()
            if keys is not None:
                self._keys, self._fetched_at = keys, now
                self._backoff, self._retry_at = 0.0, -math.inf
            else:
                self._backoff = min(
                    max(2 * self._backoff, JWKS_RETRY_FIRST_SECONDS), JWKS_RETRY_MAX_SECONDS
                )
                self._retry_at = now + self._backoff
                if dropped:
                    self._keys = None
            self._fetch = None
            fetch.keys = keys
            fetch.done.set()
            return self._held(now)

    def _wait_for(self, fetch: _Fetch) -> PyJWKSet:
        """The set ``fetch`` brings, waited for no longer than what is left of its timeout — or,
        when it brings none, the set still held."""
        fetch.done.wait(max(0.0, fetch.deadline - self._clock()))
        if fetch.keys is not None:
            return fetch.keys
        with self._state:
            held = self._held(self._clock())
        if held is None:
            raise PyJWKClientConnectionError("The key set could not be fetched in time.")
        return held

    def _report_kept(self, exc: Exception) -> None:
        """Say that tokens are verified against a kept set, and how much longer they will be."""
        with self._state:
            age = self._clock() - self._fetched_at
            retry = self._backoff
        _log.warning(
            "oidc_jwks_unavailable",
            jwks_uri=self.uri,
            error=str(exc),
            error_type=type(exc).__name__,
            key_set_age_seconds=round(age),
            retry_in_seconds=retry,
            detail=(
                "The identity provider's key set could not be fetched again, so tokens are "
                f"verified against the one fetched {age:.0f}s ago until it is "
                f"{self._stale_limit:.0f}s old. A key the provider withdrew since is accepted "
                "until then; after it, every token is refused until a fetch succeeds."
            ),
        )


def build_jwks_client(jwks_uri: str, timeout: float = DEFAULT_JWKS_TIMEOUT_SECONDS) -> PyJWKClient:
    """A caching JWKS client for ``jwks_uri``: a **bounded** fetch (see
    :data:`DEFAULT_JWKS_TIMEOUT_SECONDS`), one at a time, a key set that outlives the provider's
    outage, and a bounded refetch for unknown key ids (:class:`BoundedRefreshJwkClient`)."""
    return BoundedRefreshJwkClient(jwks_uri, timeout=timeout)
