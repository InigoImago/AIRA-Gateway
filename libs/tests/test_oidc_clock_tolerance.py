"""Two tolerances, and they are not one setting (`FRD-134`).

`JwtVerifier` passed no `leeway`, so PyJWT used `0` — a number nobody chose, undocumented and
untested. Read as security that is the strict end and defensible; read as availability it is not,
because of an interaction with a hardening decision taken deliberately elsewhere: `iat` is a
**required** claim here, and PyJWT refuses a token whose `iat` lies in the future. A verifier one
second behind its issuer therefore refuses **every** freshly minted token, at the first call, as a
`401` that is indistinguishable from a wrong secret.

Every case below fixes the *issuer's* clock and moves the token, because that is the direction the
failure comes from: the claims are stamped by Keycloak and compared against this host.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.oidc import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    DEFAULT_EXPIRY_LEEWAY_SECONDS,
    MAX_TOLERANCE_SECONDS,
    JwtVerifier,
    ToleranceOutOfRange,
)

ISSUER = "https://keycloak.test/realms/aira"


class _Resolver:
    """A JWKS stand-in that answers with the one key these tests sign with."""

    def __init__(self, public: Any) -> None:
        self._key = type("Key", (), {"key": public})()

    def get_signing_key_from_jwt(self, token: str) -> Any:
        del token
        return self._key


@pytest.fixture(scope="module")
def keys() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _token(private: Any, *, iat_offset: float = 0.0, lifetime: float = 300.0) -> str:
    """A token stamped by an issuer whose clock differs from ours by ``iat_offset`` seconds."""
    issued = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=iat_offset)
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": "u-1",
            "iat": issued,
            "exp": issued + dt.timedelta(seconds=lifetime),
        },
        private,
        algorithm="RS256",
    )


def _verifier(public: Any, **tolerances: float) -> JwtVerifier:
    return JwtVerifier(ISSUER, None, _Resolver(public), **tolerances)  # type: ignore[arg-type]


def test_a_token_from_a_clock_ahead_of_ours_is_accepted_by_default(keys: tuple[Any, Any]) -> None:
    """The defect this closes. Five seconds is an ordinary difference between two hosts."""
    private, public = keys

    assert _verifier(public).verify(_token(private, iat_offset=5)) is not None


def test_the_same_token_is_refused_with_no_tolerance(keys: tuple[Any, Any]) -> None:
    """What every deployment did until 2026-08-17 — and it refuses *fresh* tokens, not stale ones,
    which is why it reads as a broken credential rather than as a clock."""
    private, public = keys

    assert _verifier(public, clock_skew_seconds=0).verify(_token(private, iat_offset=5)) is None


def test_an_expired_token_is_refused_at_the_defaults(keys: tuple[Any, Any]) -> None:
    """The half that is **not** conceded. The predecessor grants 60 seconds past `exp`; we do not,
    because that extends a credential's life rather than tolerating a clock."""
    private, public = keys
    expired = _token(private, iat_offset=-120, lifetime=60)

    assert _verifier(public).verify(expired) is None


def test_the_two_settings_are_separate(keys: tuple[Any, Any]) -> None:
    """The property the expiry re-check exists for, and the one a refactor would delete.

    `decode` is given the clock skew, which covers `exp` as well — so with a 60-second skew and no
    second check, a token a minute past its expiry would verify. That is precisely the concession
    this design refuses by default, and without the re-check it would arrive as a side effect of
    fixing the clock problem.
    """
    private, public = keys
    expired = _token(private, iat_offset=-120, lifetime=90)  # 30 seconds past `exp`

    assert _verifier(public, clock_skew_seconds=60).verify(expired) is None
    assert _verifier(public, clock_skew_seconds=60, expiry_leeway_seconds=60).verify(expired)


def test_a_tolerance_beyond_a_token_lifetime_is_refused_at_construction() -> None:
    """Startup, not per request: a service that answers with a second lifetime for every token is
    up, healthy, and wrong."""
    with pytest.raises(ToleranceOutOfRange) as skew:
        JwtVerifier(ISSUER, None, _Resolver(object()), clock_skew_seconds=MAX_TOLERANCE_SECONDS + 1)  # type: ignore[arg-type]
    assert "AIRA_OIDC_CLOCK_SKEW_SECONDS" in str(skew.value)

    with pytest.raises(ToleranceOutOfRange) as expiry:
        JwtVerifier(ISSUER, None, _Resolver(object()), expiry_leeway_seconds=-1)  # type: ignore[arg-type]
    assert "AIRA_OIDC_EXPIRY_LEEWAY_SECONDS" in str(expiry.value)


def test_the_defaults_are_the_ones_argued_for() -> None:
    """A default is the decision every installation gets; naming it in a test is what stops it
    drifting back to a library's."""
    assert DEFAULT_CLOCK_SKEW_SECONDS == 60.0
    assert DEFAULT_EXPIRY_LEEWAY_SECONDS == 0.0
