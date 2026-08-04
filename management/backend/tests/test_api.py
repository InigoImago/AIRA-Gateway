import datetime as dt

import aira_management.apps.api.authentication as authentication
import jwt
import pytest
from aira_management.config.app_settings import ManagementSettings
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.oidc import JwtVerifier

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
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(private, roles=["global-admin"], groups=["/use-cases/demo-uc"])

    resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subject"] == "u-1"
    assert data["username"] == "demo-user"
    assert data["email"] == "demo-user@demo.aira"
    assert "global-admin" in data["roles"]
    assert "/use-cases/demo-uc" in data["use_cases"]


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
