import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.roles import parse_role_groups
from aira_gateway.auth.oidc import OidcValidator, build_oidc_validator
from aira_gateway.config import GatewaySettings

ISSUER = "https://kc.example/realms/aira"


@pytest.fixture
def role_groups():
    """The mapping an installation configures (`ADR-0017`). Parsed from the same string a
    deployment sets, rather than hand-built, so a test cannot pass against a mapping the parser
    would have refused."""
    return parse_role_groups(
        "global-admin=/aira/global-admins;it-security=/aira/it-security;"
        "it-steuerung=/aira/it-steuerung"
    )


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
    iat: bool = True,
    exp: bool = True,
    groups: list[str] | None = None,
    roles: list[str] | None = None,
    expired: bool = False,
) -> str:
    now = dt.datetime.now(dt.UTC)
    delta = dt.timedelta(minutes=-5) if expired else dt.timedelta(minutes=5)
    claims: dict[str, object] = {"iss": iss}
    if iat:
        claims["iat"] = now
    if exp:
        claims["exp"] = now + delta
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


def test_a_configured_group_confers_its_role(role_groups) -> None:
    """**Rewritten, not repaired (`ADR-0017`).** It used to assert that `realm_access.roles`
    reached the principal; roles now come from group membership and nothing else, so the property
    it guarded no longer exists and a patched version would have tested a contract nobody has."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, groups=["/aira/it-steuerung"]))

    assert principal is not None
    assert principal.roles == ("it-steuerung",)
    assert principal.is_governance is True


def test_a_realm_role_on_the_token_confers_nothing(role_groups) -> None:
    """The point of the change, stated as the thing that must **not** happen. A realm role is no
    longer read, so an administrator who assigns one directly has granted nothing — that is the
    guarantee the owner asked for, and the only way to know it holds is to send one."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, roles=["global-admin", "it-steuerung"]))

    assert principal is not None
    assert principal.roles == ()
    assert principal.is_governance is False
    assert principal.is_oversight is False


def test_a_group_the_configuration_does_not_name_confers_nothing(role_groups) -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, groups=["/aira/somebody-elses-admins"]))

    assert principal is not None
    assert principal.roles == ()


def test_without_a_mapping_nobody_holds_a_role() -> None:
    """An unconfigured gateway withholds oversight rather than assuming it. Reading a token that
    carries every group in the realm must not make somebody governance by default."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    principal = validator.validate(
        _token(private, groups=["/aira/global-admins"], roles=["global-admin"])
    )

    assert principal is not None
    assert principal.roles == ()


def test_a_token_without_groups_yields_a_principal_with_no_roles() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    principal = validator.validate(_token(private))

    assert principal is not None
    assert principal.roles == ()
    assert principal.is_governance is False


# ---- required claims (2026-08-08) -----------------------------------------------------------
#
# PyJWT verifies `exp` when it is *present* and accepts a token carrying none at all. A token
# minted without one — or with the claim stripped anywhere between the issuer and here — was
# therefore a credential that never expired, and nothing in the gateway would have noticed.
# Absence of information is not permission; the same rule as "unpriced is not free".


def test_a_token_with_no_expiry_is_refused() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    assert validator.validate(_token(private, exp=False)) is None


def test_a_token_with_no_issued_at_is_refused() -> None:
    """`iat` is what makes "this token predates the incident" an answerable question."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    assert validator.validate(_token(private, iat=False)) is None


def test_a_token_with_no_subject_is_refused() -> None:
    """`sub` is what every audit row, membership decision and budget booking is attributed to."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    assert validator.validate(_token(private, sub=None)) is None


def test_an_ordinary_keycloak_token_is_unaffected() -> None:
    """The other half of the change: a real realm sends all three, so nothing legitimate breaks."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))

    assert validator.validate(_token(private)) is not None
