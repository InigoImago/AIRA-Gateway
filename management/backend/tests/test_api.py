import datetime as dt

import aira_management.apps.api.authentication as authentication
import jwt
import pytest
from aira_management.config.app_settings import ManagementSettings
from aira_management.rbac import sync_user_roles
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.oidc import JwtVerifier

from .conftest import role_claims

pytestmark = pytest.mark.django_db

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
    sub: str = "u-1",
    username: str = "demo-user",
    email: str = "demo-user@demo.aira",
    roles: list[str] | None = None,
    groups: list[str] | None = None,
) -> str:
    now = dt.datetime.now(dt.UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "sub": sub,
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
        "preferred_username": username,
        "email": email,
    }
    if roles is not None:
        claims["realm_access"] = {"roles": roles}
    if groups is not None:
        claims["groups"] = groups
    return jwt.encode(claims, private, algorithm="RS256")


def _use_fake_verifier(monkeypatch, public: object) -> None:
    monkeypatch.setattr(
        authentication,
        "build_management_verifier",
        lambda: JwtVerifier(ISSUER, None, _Resolver(public)),
    )


def test_me_requires_auth() -> None:
    resp = APIClient().get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthenticated"


def test_me_with_valid_token(monkeypatch) -> None:
    """**Rewritten for `ADR-0017`.** The token used to carry `realm_access.roles` and this asserted
    the claim came back. A realm role grants nothing now, so the role arrives through the group —
    and the response reports slugs rather than the raw group list."""
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(private, groups=["/aira/global-admins", "/use-cases/demo-uc"])

    resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subject"] == "u-1"
    assert data["username"] == "demo-user"
    assert data["email"] == "demo-user@demo.aira"
    assert "global-admin" in data["roles"]
    assert data["use_cases"] == ["demo-uc"]


def test_me_states_the_currency_the_installation_is_configured_with(monkeypatch) -> None:
    """The unit every money figure on the console is in, from the one setting that decides it.

    Three screens said *"US dollars"* in so many words while `AIRA_CURRENCY` labelled the same
    numbers in every CSV — a setting with exactly one reader, on the other plane. Asserted against
    an installation that is **not** on the default, because a test on `EUR` would pass against a
    hard-coded string too.
    """
    from aira_management.config.runtime import get_settings

    from .test_apikeys import override_settings_value

    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(private)

    with override_settings_value(currency="CHF"):
        resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert resp.json()["currency"] == "CHF"
    assert get_settings().currency != "CHF", "the override leaked and the assertion proves nothing"


def test_provisioning_is_idempotent(monkeypatch) -> None:
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(private)
    client = APIClient()
    client.get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    client.get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert get_user_model().objects.filter(username="demo-user").count() == 1


def test_invalid_token_rejected(monkeypatch) -> None:
    private, public = _keypair()
    other_private, _ = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(other_private)  # signed with a different key

    resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 401


def test_oidc_not_configured_rejects_bearer() -> None:
    resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION="Bearer whatever")
    assert resp.status_code == 401


def test_build_verifier_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        authentication, "get_settings", lambda: ManagementSettings(oidc_issuer=ISSUER)
    )
    authentication.build_management_verifier.cache_clear()
    try:
        assert authentication.build_management_verifier() is not None
    finally:
        authentication.build_management_verifier.cache_clear()


def test_build_verifier_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(authentication, "get_settings", lambda: ManagementSettings(oidc_issuer=""))
    authentication.build_management_verifier.cache_clear()
    try:
        assert authentication.build_management_verifier() is None
    finally:
        authentication.build_management_verifier.cache_clear()


def test_exception_handler_passes_through_unhandled() -> None:
    from aira_management.apps.api.exceptions import exception_handler

    # DRF does not handle a plain exception -> our handler returns None (Django 500 path).
    assert exception_handler(RuntimeError("boom"), {}) is None


def test_exception_handler_wraps_field_errors() -> None:
    from aira_management.apps.api.exceptions import exception_handler
    from rest_framework.exceptions import ValidationError

    response = exception_handler(ValidationError({"slug": ["invalid"]}), {})
    assert response is not None
    assert response.data["error"]["code"] == "invalid_argument"
    assert response.data["error"]["details"] == {"slug": ["invalid"]}


# ---- /me reports what the server enforces (ADR-0017) --------------------------------------


def test_me_reports_the_roles_the_server_enforces_not_the_token_claim() -> None:
    """**Found live**: a Global Administrator was shown no "New use case" button.

    This view read `realm_access.roles` straight off the claim, which made it a *third* answer to
    "which roles does this caller hold" beside `sync_user_roles` and the permission classes. While
    all three read the same claim they agreed by accident. The moment roles came from group
    membership they did not: the server let the caller through and the console was told they had
    no roles at all.
    """
    user = get_user_model().objects.create(username="me-user")
    sync_user_roles(user, role_claims("global-admin"))
    client = APIClient()
    client.force_authenticate(user=user, token={"sub": "s", "groups": ["/aira/global-admins"]})

    body = client.get("/api/v1/me").json()

    assert body["roles"] == ["global-admin"]


def test_me_reports_use_case_slugs_rather_than_every_group_the_token_carries() -> None:
    """It returned the whole `groups` claim. That was loose before and is wrong now: the claim
    also carries the role groups, so a console asking "which use cases am I in" was told
    `/aira/global-admins`."""
    user = get_user_model().objects.create(username="me-slugs")
    sync_user_roles(user, role_claims("global-admin"))
    client = APIClient()
    client.force_authenticate(
        user=user,
        token={
            "sub": "s",
            "groups": ["/aira/global-admins", "/use-cases/demo-uc", "/abteilungen/x"],
        },
    )

    body = client.get("/api/v1/me").json()

    assert body["use_cases"] == ["demo-uc"]
