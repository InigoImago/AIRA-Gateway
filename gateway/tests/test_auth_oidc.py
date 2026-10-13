import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.permissions import Permission
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
    # `object`, not `list[str]`: the case worth testing is a claim that is **not** a list, which a
    # narrower annotation would have made unwritable — and unwritten is how the branch that
    # narrows it came to be undefended.
    groups: object = None,
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


@pytest.mark.parametrize(
    ("claim", "why"),
    [
        pytest.param(
            "/aira/global-admins",
            "a single-valued group mapper sends the path as a bare string, and iterating a string "
            "yields its characters — 20 group paths one letter long",
            id="a-string",
        ),
        pytest.param(
            {"/aira/global-admins": True},
            "a mapper configured to emit an object; its keys look enough like paths to be used",
            id="an-object",
        ),
        pytest.param(
            7,
            "not iterable at all, so the claim raises inside authentication rather than conferring "
            "nothing",
            id="a-number",
        ),
    ],
)
def test_a_malformed_groups_claim_confers_nothing_rather_than_failing(
    role_groups, claim: object, why: str
) -> None:
    """A realm misconfiguration must stop **authority**, never authentication (`ADR-0017`).

    Groups are the single source of both organisation-wide roles and use-case access, so this one
    claim decides everything a token may do. `validate` narrows it to a list before reading it, and
    until now nothing proved that line did anything: every test passed a well-formed list.

    The three cases are the three shapes a real directory produces, and they fail differently —
    which is why the assertion is on all three answers rather than on "it did not raise". A string
    confers no role and no use case and still puts twenty single characters into `groups`, where
    the group-grant resolver would then look every one of them up in the read-model; a number does
    not survive the first `for` at all.
    """
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, groups=claim))

    assert principal is not None, f"authentication failed on {why}"
    assert principal.use_cases == ()
    assert principal.groups == ()
    assert principal.roles == ()


def test_missing_subject_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate(_token(private, sub=None)) is None


def test_malformed_token_rejected() -> None:
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    assert validator.validate("not.a.jwt") is None


def test_a_payload_nested_past_the_parser_is_rejected_not_raised() -> None:
    """Routing reads the unverified `iss`, so the payload is parsed before any verifier runs. A
    payload nested deeper than `json` follows raises `RecursionError`, which PyJWT does not wrap,
    and a worker thread with a small stack reaches that depth with a token of a few kilobytes."""
    import base64
    import threading

    _private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public))
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(b"[" * 5000).rstrip(b"=").decode()
    outcome: list[object] = []

    def _validate() -> None:
        try:
            outcome.append(validator.validate(f"{header}.{payload}.c2ln"))
        except BaseException as exc:  # noqa: BLE001 - what escapes is the finding
            outcome.append(exc)

    previous = threading.stack_size(256 * 1024)
    try:
        worker = threading.Thread(target=_validate)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous)

    assert outcome == [None]


def test_build_validator_disabled() -> None:
    assert build_oidc_validator(GatewaySettings(oidc_enabled=False)) is None
    assert build_oidc_validator(GatewaySettings(oidc_enabled=True, oidc_issuer="")) is None


def test_build_validator_enabled() -> None:
    validator = build_oidc_validator(GatewaySettings(oidc_enabled=True, oidc_issuer=ISSUER))
    assert validator is not None


def test_build_validator_with_audience_skips_the_warning() -> None:
    """An explicit audience is the configuration we want; no warning path is taken — and without
    one the warning is given, or this would pass on either path."""
    from structlog.testing import capture_logs

    with capture_logs() as named:
        validator = build_oidc_validator(
            GatewaySettings(oidc_enabled=True, oidc_issuer=ISSUER, oidc_audience="aira-gateway")
        )
    with capture_logs() as unnamed:
        build_oidc_validator(GatewaySettings(oidc_enabled=True, oidc_issuer=ISSUER))

    assert validator is not None
    assert not [event for event in named if event["event"] == "oidc_audience_unset"]
    assert [event for event in unnamed if event["event"] == "oidc_audience_unset"]


# ---- what the one `algorithms=("RS256",)` line defends, and the settings that reach it -------


def _segment(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _claims(**overrides: object) -> dict[str, object]:
    now = dt.datetime.now(dt.UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "sub": "user-123",
        "aud": "aira-gateway",
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def test_a_token_mac_ed_with_the_realms_public_key_is_refused() -> None:
    """Algorithm confusion: HS256 keyed with the RSA public key, which anybody can fetch. A verifier
    that let the token choose its algorithm would check the MAC with that same public key."""
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    _private, public = _keypair()
    secret = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signing_input = (
        f"{_segment(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())}."
        f"{_segment(json.dumps(_claims()).encode())}"
    )
    mac = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()

    validator = OidcValidator(ISSUER, "aira-gateway", _Resolver(public))
    assert validator.validate(f"{signing_input}.{_segment(mac)}") is None


def test_an_unsigned_token_is_refused() -> None:
    _private, public = _keypair()
    unsigned = jwt.encode(_claims(), None, algorithm="none")

    assert OidcValidator(ISSUER, "aira-gateway", _Resolver(public)).validate(unsigned) is None


def test_a_token_without_an_audience_is_refused_where_one_is_configured() -> None:
    private, public = _keypair()
    token = jwt.encode(_claims(aud=None), private, algorithm="RS256")

    assert OidcValidator(ISSUER, "aira-gateway", _Resolver(public)).validate(token) is None
    assert OidcValidator(ISSUER, None, _Resolver(public)).validate(token) is not None


@pytest.mark.parametrize(
    ("setting", "value", "offsets", "accepted"),
    [
        ("oidc_clock_skew_seconds", 5.0, {"iat": 30}, False),
        ("oidc_clock_skew_seconds", 60.0, {"iat": 30}, True),
        ("oidc_expiry_leeway_seconds", 0.0, {"exp": -20}, False),
        ("oidc_expiry_leeway_seconds", 60.0, {"exp": -20}, True),
    ],
    ids=["skew-too-small", "skew-enough", "no-leeway", "leeway-enough"],
)
def test_the_clock_tolerances_reach_the_verifier_from_the_settings(
    setting: str, value: float, offsets: dict[str, int], accepted: bool
) -> None:
    """Both halves were tested — the settings parse, `JwtVerifier` honours what it is handed — and
    the wire between them, `build_oidc_validator`, was not."""
    private, public = _keypair()
    now = int(dt.datetime.now(dt.UTC).timestamp())
    expires = now + offsets["exp"] if "exp" in offsets else now + 300
    issued = now + offsets.get("iat", -60)
    token = jwt.encode(_claims(iat=issued, exp=expires), private, algorithm="RS256")

    validator = build_oidc_validator(
        GatewaySettings(
            oidc_enabled=True, oidc_issuer=ISSUER, oidc_audience="aira-gateway", **{setting: value}
        )
    )
    assert validator is not None
    for _issuer, verifier in validator._verifiers:
        verifier._jwks = _Resolver(public)  # the key set is not what is under test

    assert (validator.validate(token) is not None) is accepted


def test_a_configured_group_confers_its_role(role_groups) -> None:
    """**Rewritten, not repaired (`ADR-0017`).** It used to assert that `realm_access.roles`
    reached the principal; roles now come from group membership and nothing else, so the property
    it guarded no longer exists and a patched version would have tested a contract nobody has."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, groups=["/aira/it-steuerung"]))

    assert principal is not None
    assert principal.roles == ("it-steuerung",)
    assert principal.allows(Permission.USECASE_READ_RETIRED) is True


def test_a_realm_role_on_the_token_confers_nothing(role_groups) -> None:
    """The point of the change, stated as the thing that must **not** happen. A realm role is no
    longer read, so an administrator who assigns one directly has granted nothing — that is the
    guarantee the owner asked for, and the only way to know it holds is to send one."""
    private, public = _keypair()
    validator = OidcValidator(ISSUER, None, _Resolver(public), role_groups=role_groups)

    principal = validator.validate(_token(private, roles=["global-admin", "it-steuerung"]))

    assert principal is not None
    assert principal.roles == ()
    assert principal.allows(Permission.USECASE_READ_RETIRED) is False
    assert principal.allows(Permission.USECASE_READ_ALL) is False


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
    assert principal.allows(Permission.USECASE_READ_RETIRED) is False


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
