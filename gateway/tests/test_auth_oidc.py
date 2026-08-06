import datetime as dt

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_gateway.auth.oidc import OidcValidator, build_oidc_validator
from aira_gateway.config import GatewaySettings

ISSUER = "https://kc.example/realms/aira"


def _keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


class _Resolver:
    def __init__(self, public_key: object) -> None:
        self._public = public_key

    def get_signing_key_from_jwt(self, token: str) -> object:  # noqa: ARG002
        resolver_self = self

        class _Key:
            key = resolver_self._public

        return _Key()


def _token(
    private: rsa.RSAPrivateKey,
    *,
    iss: str = ISSUER,
    aud: str | None = None,
    sub: str | None = "user-123",
    groups: list[str] | None = None,
    roles: list[str] | None = None,
    expired: bool = False,
) -> str:
    now = dt.datetime.now(dt.UTC)
    delta = dt.timedelta(minutes=-5) if expired else dt.timedelta(minutes=5)
    claims: dict[str, object] = {"iss": iss, "iat": now, "exp": now + delta}
    if sub is not None:
        claims["sub"] = sub
    if aud is not None:
        claims["aud"] = aud
    if groups is not None:
        claims["groups"] = groups
    if roles is not None:
        claims["realm_access"] = {"roles": roles}
    return jwt.encode(claims, private, algorithm="RS256")


def test_valid_token_resolves_principal() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    principal = validator.validate(_token(private))
    assert principal is not None
    assert principal.subject == "user-123"
    assert principal.method == "oidc"


def test_expired_token_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate(_token(private, expired=True)) is None


def test_wrong_issuer_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate(_token(private, iss="https://evil")) is None


def test_bad_signature_rejected() -> None:
    private, _ = _keypair()
    _, other_public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(other_public))
    assert validator.validate(_token(private)) is None


def test_audience_enforced_when_configured() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, "aira-gateway", _Resolver(public))
    assert validator.validate(_token(private, aud="aira-gateway")) is not None
    assert validator.validate(_token(private, aud="other")) is None


def test_groups_become_use_cases() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    principal = validator.validate(
        _token(private, groups=["/use-cases/demo-uc", "/use-cases/other-uc", "/random"])
    )
    assert principal is not None
    assert principal.use_cases == ("demo-uc", "other-uc")


def test_no_groups_claim_yields_no_use_cases() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    principal = validator.validate(_token(private))
    assert principal is not None
    assert principal.use_cases == ()


def test_missing_subject_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate(_token(private, sub=None)) is None


def test_malformed_token_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate("not.a.jwt") is None


def test_build_validator_disabled() -> None:
    assert build_oidc_validator(GatewaySettings(oidc_enabled=False)) is None
    assert build_oidc_validator(GatewaySettings(oidc_enabled=True, oidc_issuer="")) is None


def test_build_validator_enabled() -> None:
    validator = build_oidc_validator(GatewaySettings(oidc_enabled=True, oidc_issuer=ISSUER))
    assert validator is not None


def test_build_validator_with_audience_skips_the_warning() -> None:
    """An explicit audience is the configuration we want; no warning path is taken."""
    validator = build_oidc_validator(
        GatewaySettings(oidc_enabled=True, oidc_issuer=ISSUER, oidc_audience="aira-gateway")
    )
    assert validator is not None


def test_the_realm_roles_reach_the_principal() -> None:
    """The claim is already in the token; before ADR-0009 the validator simply dropped it, and a
    governance caller was indistinguishable from someone with no memberships at all."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    principal = validator.validate(_token(private, roles=["it-steuerung", "use-case-user"]))

    assert principal is not None
    assert principal.roles == ("it-steuerung", "use-case-user")
    assert principal.is_governance is True


def test_a_token_without_roles_yields_a_principal_with_none() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    principal = validator.validate(_token(private))

    assert principal is not None
    assert principal.roles == ()
    assert principal.is_governance is False
