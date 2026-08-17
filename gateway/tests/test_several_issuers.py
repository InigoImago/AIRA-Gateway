"""Tokens from more than one Keycloak realm (`FRD-118` FR-1).

One organisation, several realms — a migration between them, a second instance, a merger. The
owner's answer (2026-08-17): the realms describe **one population**, so the same group path from
either means the same thing. That is a configuration list, not an identity change; two *unrelated*
directories would need the issuer to be part of the identity and is a different feature.

What is worth testing here is not "two verifiers exist" but the two things that decide whether this
is safe and whether it is usable: a token is verified by the realm it names and **not** merely
accepted because some realm was willing, and a token from realm B does not make realm A refetch its
key set on every request.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_gateway.auth.oidc import OidcValidator

A = "https://kc-a.test/realms/aira"
B = "https://kc-b.test/realms/aira"


class _Resolver:
    """One realm's key set, counting how often it was asked."""

    def __init__(self, public: Any) -> None:
        self._key = type("Key", (), {"key": public})()
        self.asked = 0

    def get_signing_key_from_jwt(self, token: str) -> Any:
        del token
        self.asked += 1
        return self._key


@pytest.fixture(scope="module")
def realms() -> dict[str, Any]:
    a = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    b = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {"a": a, "b": b}


def _token(private: Any, issuer: str, *, sub: str = "u-1", groups: list[str] | None = None) -> str:
    now = dt.datetime.now(dt.UTC)
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
        "aud": "aira-gateway",
        "preferred_username": "erika",
    }
    if groups is not None:
        claims["groups"] = groups
    return jwt.encode(claims, private, algorithm="RS256")


def _validator(realms: dict[str, Any]) -> tuple[OidcValidator, _Resolver, _Resolver]:
    keys_a = _Resolver(realms["a"].public_key())
    keys_b = _Resolver(realms["b"].public_key())
    validator = OidcValidator(
        issuer=A,
        audience="aira-gateway",
        jwks=keys_a,  # type: ignore[arg-type]
        others=((B, "aira-gateway", keys_b),),  # type: ignore[arg-type]
    )
    return validator, keys_a, keys_b


def test_a_token_from_either_realm_is_accepted(realms: dict[str, Any]) -> None:
    validator, _, _ = _validator(realms)

    first = validator.validate(_token(realms["a"], A))
    second = validator.validate(_token(realms["b"], B))

    assert first is not None and first.issuer == A
    assert second is not None and second.issuer == B


def test_the_same_group_path_means_the_same_thing_in_both(realms: dict[str, Any]) -> None:
    """The owner's decision made testable. One population, so `/use-cases/x` grants `x` from either
    realm — and if that ever stops being true, this is the test that has to be argued with."""
    validator, _, _ = _validator(realms)

    from_a = validator.validate(_token(realms["a"], A, groups=["/use-cases/kundenservice"]))
    from_b = validator.validate(_token(realms["b"], B, groups=["/use-cases/kundenservice"]))

    assert from_a is not None and from_b is not None
    assert from_a.use_cases == from_b.use_cases == ("kundenservice",)


def test_a_token_signed_by_one_realm_but_naming_another_is_refused(realms: dict[str, Any]) -> None:
    """The property that makes routing by an unverified claim safe.

    `iss` selects *which* verifier runs; the verifier then checks `iss` and the signature for real.
    A token signed with realm A's key while claiming to come from realm B must be refused by both:
    B's verifier has the wrong key, and A's has the wrong issuer.
    """
    validator, _, _ = _validator(realms)

    assert validator.validate(_token(realms["a"], B)) is None


def test_a_token_naming_no_configured_realm_is_refused(realms: dict[str, Any]) -> None:
    validator, _, _ = _validator(realms)

    assert validator.validate(_token(realms["a"], "https://elsewhere.test/realms/x")) is None


def test_one_realms_traffic_does_not_make_the_other_fetch_its_keys(realms: dict[str, Any]) -> None:
    """The reason routing is by `iss` rather than by probing every verifier.

    A probe asks each key set for a key id it will never hold, and PyJWT answers an unknown `kid`
    by **refetching the whole set** — so a token from realm B would cost realm A a remote call on
    every single request, added by a feature meant to be invisible.
    """
    validator, keys_a, keys_b = _validator(realms)

    for _ in range(5):
        assert validator.validate(_token(realms["b"], B)) is not None

    assert keys_b.asked == 5
    assert keys_a.asked == 0


def test_a_refusal_by_the_named_realm_stops_there(realms: dict[str, Any]) -> None:
    """A token that names realm A and fails A's checks is refused without troubling realm B.

    Otherwise every expired or malformed token from one realm would fan out into a key-set refresh
    at every other — the cheapest denial of service there is, against our own upstream.
    """
    validator, keys_a, keys_b = _validator(realms)
    expired = jwt.encode(
        {
            "iss": A,
            "sub": "u-1",
            "iat": dt.datetime.now(dt.UTC) - dt.timedelta(hours=2),
            "exp": dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            "aud": "aira-gateway",
        },
        realms["a"],
        algorithm="RS256",
    )

    assert validator.validate(expired) is None
    assert keys_a.asked == 1
    assert keys_b.asked == 0
