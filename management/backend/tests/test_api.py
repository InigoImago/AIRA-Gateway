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

from .conftest import role_claims, setting

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


def test_me_states_the_installations_clock(monkeypatch) -> None:
    """The zone every timestamp on the console is printed on (`FRD-629`), from the setting both
    planes read — not the browser's, which put a request at 00:30 beside a budget day it was not
    counted in."""
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    token = _token(private)

    with setting(time_zone="Europe/Berlin"):
        resp = APIClient().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert resp.json()["time_zone"] == "Europe/Berlin"


def test_every_timestamp_the_api_answers_is_written_in_utc() -> None:
    """A serializer field answers in Django's `TIME_ZONE`, whose default is America/Chicago."""
    from django.conf import settings
    from rest_framework import serializers

    field = serializers.DateTimeField()

    assert settings.TIME_ZONE == "UTC"
    assert field.to_representation(dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)) == (
        "2026-10-01T12:00:00Z"
    )


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


def test_an_unexpected_exception_answers_in_the_envelope_and_says_nothing_of_itself() -> None:
    """Django's own answer is an HTML page, which the console's `errorMessage` cannot read; the
    exception's text can name a column or a value, so it is logged and never answered."""
    from aira_management.apps.api.exceptions import exception_handler

    response = exception_handler(RuntimeError("boom: secret_column=42"), {})

    assert response.status_code == 500
    assert response.data == {
        "error": {"code": "internal_error", "message": "Internal server error.", "details": None}
    }


def test_a_view_that_fails_unexpectedly_answers_json(monkeypatch) -> None:
    """The same, through a real route: the handler is the one DRF is configured with."""
    from aira_management.apps.api import views

    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "role_definitions", boom)
    user = get_user_model().objects.create(username="boom-user")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/me")

    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"
    assert response.json()["error"]["code"] == "internal_error"


def test_a_status_without_a_public_code_takes_the_exceptions_own() -> None:
    """`503` is not one of the gateway's codes; the exception's `default_code` says more than a
    generic word."""
    from aira_management.apps.api.exceptions import exception_handler
    from aira_management.apps.roles.views import DirectoryCannotVerify

    response = exception_handler(DirectoryCannotVerify(), {})

    assert response.status_code == 503
    assert response.data["error"]["code"] == "directory_unavailable"


def test_a_detail_given_as_a_list_reads_as_its_sentence() -> None:
    """`str()` of a list is its repr — `[ErrorDetail(string='…', code='invalid')]` — which is what a
    reader saw for every `{"detail": [sentence]}` a view raised."""
    from aira_management.apps.api.exceptions import exception_handler
    from rest_framework.exceptions import ValidationError

    response = exception_handler(ValidationError({"detail": ["Try again in 3 day(s)."]}), {})

    assert response.data["error"]["message"] == "Try again in 3 day(s)."


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


# ---- nothing a directory can put in a claim makes a sign-in fail (`FRD-613` FR-7) ------------


def _signed(private: rsa.RSAPrivateKey, **claims: object) -> str:
    """A token carrying exactly ``claims`` beside the ones verification requires."""
    now = dt.datetime.now(dt.UTC)
    body: dict[str, object] = {
        "iss": ISSUER,
        "sub": "u-claims",
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
        "preferred_username": "claims-user",
    }
    body.update(claims)
    return jwt.encode(body, private, algorithm="RS256")


def test_a_sign_in_survives_a_group_path_longer_than_a_mirror_groups_name(monkeypatch) -> None:
    """Every token group is mirrored as a Django group, whose name is 150 characters. A deeper
    department used to make every request of everybody in it a `500` on PostgreSQL, whatever they
    did; the mirror is now named within its column, and the sign-in proceeds."""
    from aira_management.rbac import GROUP_NAME_MAX_LENGTH

    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)
    deep = "/" + "/".join(["Abteilung Konsolidierung und Berichterstattung"] * 5)
    assert len(deep) > GROUP_NAME_MAX_LENGTH

    response = APIClient().get(
        "/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {_signed(private, groups=[deep])}"
    )

    assert response.status_code == 200, response.content
    user = get_user_model().objects.get(username="claims-user")
    names = list(user.groups.values_list("name", flat=True))
    assert names and all(len(name) <= GROUP_NAME_MAX_LENGTH for name in names)


@pytest.mark.parametrize("claim", [5, {"a": 1}, "/use-cases/demo-uc", None])
def test_me_reads_a_malformed_groups_claim_as_conferring_nothing(monkeypatch, claim) -> None:
    """`/me` read the raw claim while authentication read it defensively, so a realm mapper
    emitting a scalar made the console's first call a `500` while every other endpoint answered."""
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)

    response = APIClient().get(
        "/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {_signed(private, groups=claim)}"
    )

    assert response.status_code == 200, response.content
    assert response.json()["use_cases"] == []
    assert response.json()["roles"] == []


@pytest.mark.parametrize("subject", ["u\x00nul", "u" * 256])
def test_a_subject_no_account_can_be_bound_to_is_refused_as_a_token(monkeypatch, subject) -> None:
    """The identity is keyed on `sub`, in a 255-character column PostgreSQL alone enforces, and a
    NUL no PostgreSQL text can hold: either reached the database as a `500`."""
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)

    response = APIClient().get(
        "/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {_signed(private, sub=subject)}"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert not get_user_model().objects.filter(username="claims-user").exists()


def test_an_address_no_column_can_hold_is_left_out_rather_than_failing_the_sign_in(
    monkeypatch,
) -> None:
    private, public = _keypair()
    _use_fake_verifier(monkeypatch, public)

    response = APIClient().get(
        "/api/v1/me",
        HTTP_AUTHORIZATION=f"Bearer {_signed(private, email='ada\x00@example.test')}",
    )

    assert response.status_code == 200, response.content
    assert get_user_model().objects.get(username="claims-user").email == ""


@pytest.mark.parametrize("strict", [True, False], ids=["strict", "open"])
def test_me_states_whether_the_installation_runs_strict_data_mode(strict: bool) -> None:
    """The console offers no choice the mode overrides, so it must be told the mode (`FRD-627`),
    not infer it from a refusal. Both values, because a constant would pass one of them."""
    user = get_user_model().objects.create(username="anna")
    client = APIClient()
    client.force_authenticate(user=user)

    with setting(strict_data_mode=strict):
        assert client.get("/api/v1/me").json()["strict_data"] is strict
