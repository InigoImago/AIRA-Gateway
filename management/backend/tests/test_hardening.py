"""Security hardening of the management control plane (ADR-0007).

Covers the API-key issuance boundary, deployment-safety checks, the OIDC ``sub`` binding, and
the pipeline-config bounds that protect the shared gateway.
"""

import pytest
from aira_management.apps.api.authentication import KeycloakJWTAuthentication
from aira_management.apps.api.models import OidcIdentity
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.config.app_settings import DEV_SECRET_KEY, ManagementSettings
from aira_management.config.security import (
    effective_debug,
    enforce_safe_settings,
    is_local,
    unsafe_settings,
)
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"realm_access": {"roles": list(roles)}})
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_uc(admin, slug: str = "demo-uc") -> UseCase:
    _client(admin).post(BASE, {"slug": slug, "name": "Demo"}, format="json")
    return UseCase.objects.get(slug=slug)


# ---- API-key issuance is membership-gated ------------------------------------------------


def test_governance_role_cannot_mint_keys_for_a_use_case_it_only_oversees() -> None:
    """it-steuerung sees every use case (oversight) but must not get data-plane access."""
    admin = _user("uc-admin", "use-case-admin")
    _make_uc(admin, "demo-uc")
    governance = _user("gov", "it-steuerung")

    # Oversight visibility is intact ...
    assert _client(governance).get(f"{BASE}demo-uc/").status_code == 200
    # ... but issuing a key is not.
    resp = _client(governance).post(f"{BASE}demo-uc/api-keys/", {"label": "x"}, format="json")
    assert resp.status_code == 403
    assert ApiKey.objects.count() == 0


def test_member_may_still_issue_a_key() -> None:
    admin = _user("uc-admin2", "use-case-admin")
    usecase = _make_uc(admin, "member-uc")
    member = _user("member", "use-case-user")
    _client(admin).post(
        f"{BASE}member-uc/members/", {"username": "member", "role": "user"}, format="json"
    )
    assert UseCaseMembership.objects.filter(use_case=usecase, user=member).exists()

    resp = _client(member).post(f"{BASE}member-uc/api-keys/", {"label": "cli"}, format="json")
    assert resp.status_code == 201


def test_global_admin_may_issue_a_key() -> None:
    admin = _user("uc-admin3", "use-case-admin")
    _make_uc(admin, "ga-uc")
    resp = _client(_user("ga", "global-admin")).post(
        f"{BASE}ga-uc/api-keys/", {"label": "cli"}, format="json"
    )
    assert resp.status_code == 201


# ---- deployment safety --------------------------------------------------------------------


def test_local_environment_tolerates_dev_defaults() -> None:
    settings = ManagementSettings(environment="local")
    assert is_local(settings)
    assert unsafe_settings(settings) == []
    assert effective_debug(settings) is True


def test_production_rejects_dev_defaults() -> None:
    settings = ManagementSettings(environment="production")
    problems = unsafe_settings(settings)
    assert len(problems) == 3  # secret key, wildcard hosts, debug
    assert any("SECRET_KEY" in problem for problem in problems)
    assert any("ALLOWED_HOSTS" in problem for problem in problems)
    assert effective_debug(settings) is False


def test_production_with_proper_settings_is_accepted() -> None:
    settings = ManagementSettings(
        environment="production",
        secret_key="a-real-secret-from-vault",
        allowed_hosts="aira.example.com",
        debug=False,
    )
    assert unsafe_settings(settings) == []
    assert settings.secret_key != DEV_SECRET_KEY


def test_enforce_safe_settings_raises_for_unsafe_deployment() -> None:
    with pytest.raises(ImproperlyConfigured, match="Unsafe settings"):
        enforce_safe_settings(ManagementSettings(environment="production"))


def test_enforce_safe_settings_passes_locally() -> None:
    enforce_safe_settings(ManagementSettings(environment="local"))


def test_security_headers_are_present() -> None:
    response = _client(_user("headers")).get("/api/v1/me")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"


# ---- OIDC identity binding ----------------------------------------------------------------


def _provision(subject: str, username: str):
    return KeycloakJWTAuthentication._provision_user(
        subject, {"preferred_username": username, "email": f"{username}@example.test"}
    )


def test_same_subject_resolves_to_the_same_user() -> None:
    first = _provision("sub-1", "jdoe")
    second = _provision("sub-1", "jdoe-renamed")
    assert first.pk == second.pk


def test_existing_unbound_user_is_adopted_on_first_login() -> None:
    """Accounts created before the binding existed keep their permissions."""
    legacy = get_user_model().objects.create(username="legacy")
    adopted = _provision("sub-legacy", "legacy")
    assert adopted.pk == legacy.pk
    assert OidcIdentity.objects.get(subject="sub-legacy").user_id == legacy.pk


def test_reused_username_does_not_inherit_the_previous_account() -> None:
    original = _provision("sub-old", "jdoe")
    newcomer = _provision("sub-new", "jdoe")
    assert newcomer.pk != original.pk
    assert newcomer.get_username() != "jdoe"


def test_token_without_subject_is_rejected() -> None:
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework.test import APIRequestFactory

    class _Verifier:
        def verify(self, token: str) -> dict:
            return {"preferred_username": "nosub"}

    auth = KeycloakJWTAuthentication()
    request = APIRequestFactory().get("/api/v1/me", headers={"authorization": "Bearer t"})
    import aira_management.apps.api.authentication as module

    module.build_management_verifier.cache_clear()
    original = module.build_management_verifier
    module.build_management_verifier = lambda: _Verifier()
    try:
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(request)
    finally:
        module.build_management_verifier = original
        module.build_management_verifier.cache_clear()


def test_oidc_identity_str() -> None:
    user = get_user_model().objects.create(username="strtest")
    identity = OidcIdentity.objects.create(subject="sub-str", user=user)
    assert str(identity) == "sub-str -> strtest"
