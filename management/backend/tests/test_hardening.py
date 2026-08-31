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

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
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
    admin = _user("uc-admin", "global-admin")
    _make_uc(admin, "demo-uc")
    governance = _user("gov", "it-steuerung")

    # Oversight visibility is intact ...
    assert _client(governance).get(f"{BASE}demo-uc/").status_code == 200
    # ... but issuing a key is not.
    resp = _client(governance).post(f"{BASE}demo-uc/api-keys/", {"label": "x"}, format="json")
    assert resp.status_code == 403
    assert ApiKey.objects.count() == 0


def test_member_may_still_issue_a_key() -> None:
    admin = _user("uc-admin2", "global-admin")
    usecase = _make_uc(admin, "member-uc")
    member = _user("member")
    _client(admin).post(
        f"{BASE}member-uc/members/", {"username": "member", "role": "user"}, format="json"
    )
    assert UseCaseMembership.objects.filter(use_case=usecase, user=member).exists()

    resp = _client(member).post(f"{BASE}member-uc/api-keys/", {"label": "cli"}, format="json")
    assert resp.status_code == 201


def test_global_admin_may_issue_a_key() -> None:
    admin = _user("uc-admin3", "global-admin")
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
    # Every reason at once, never the first one: a configuration review that reports one problem
    # per deploy attempt is four deploys (`ADR-0015`).
    # secret key, wildcard hosts, debug, no global-admin group, plaintext Kafka
    assert len(problems) == 5
    assert any("SECRET_KEY" in problem for problem in problems)
    assert any("ALLOWED_HOSTS" in problem for problem in problems)
    assert any("AIRA_ROLE_GROUPS" in problem for problem in problems)
    assert any("AIRA_KAFKA_SECURITY_PROTOCOL" in problem for problem in problems)
    assert effective_debug(settings) is False


def test_production_with_proper_settings_is_accepted() -> None:
    settings = ManagementSettings(
        environment="production",
        secret_key="a-real-secret-from-vault",
        allowed_hosts="aira.example.com",
        debug=False,
        role_groups="global-admin=/aira/global-admins",
        kafka_security_protocol="SASL_SSL",
    )
    assert unsafe_settings(settings) == []
    assert settings.secret_key != DEV_SECRET_KEY


def test_a_deployment_with_no_global_admin_group_refuses_to_start() -> None:
    """Roles come from group membership and nothing else (`ADR-0017`), so an unnamed global-admin
    group is not a permissive default — it is a console with no administrator, discovered hours
    later as "nobody can log in properly". Local is exempt (`ADR-0015`): the demo has to start on
    a fresh checkout, and there the console states the mapping instead."""
    settings = ManagementSettings(
        environment="production",
        secret_key="a-real-secret-from-vault",
        allowed_hosts="aira.example.com",
        debug=False,
        role_groups="it-security=/aira/it-security",
        kafka_security_protocol="SASL_SSL",
    )

    problems = unsafe_settings(settings)

    assert len(problems) == 1
    assert "global-admin" in problems[0]
    assert unsafe_settings(ManagementSettings(environment="local")) == []


def test_a_malformed_role_mapping_is_reported_rather_than_raised_at_import() -> None:
    """It is a *reason*, listed beside the others, so a review sees every problem at once — and
    because raising from a settings module turns a typo into a stack trace nobody can place."""
    settings = ManagementSettings(
        environment="production",
        secret_key="a-real-secret-from-vault",
        allowed_hosts="aira.example.com",
        debug=False,
        role_groups="global-admin=/aira/admins;nonsense",
    )

    problems = unsafe_settings(settings)

    assert any("AIRA_ROLE_GROUPS is malformed" in problem for problem in problems)


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


def test_a_lost_race_returns_the_winners_user_rather_than_a_500() -> None:
    """The first request from a new person is more than one request.

    The console loads `/api/v1/me` and `/api/v1/use-cases/` at the same moment, so two requests
    carrying the same brand-new `sub` arrive together: both find no identity, both create one, and
    the second loses on `api_oidcidentity_subject_key`. That surfaced as a **500 on the first
    screen, for every user's first login** — measured against a freshly seeded stack, which is the
    state a demonstration starts from.

    `transaction.atomic` never prevented it: it makes each attempt atomic, not exclusive, and the
    two attempts are on different connections. The property is that whoever arrives second **loses
    gracefully** — re-reads, and uses the row the winner wrote.

    The race is expressed by writing the winner's row between the read and the write, which is
    exactly what the other connection does and is deterministic here.
    """
    subject = "sub-raced"
    winner = get_user_model().objects.create(username="raced-winner")
    original = OidcIdentity.objects.filter

    def _first_call_sees_nothing(*args: object, **kwargs: object):
        """Answer "no identity" once, then let the real query through — the winner has committed
        by the time the loser looks again."""
        OidcIdentity.objects.filter = original  # type: ignore[method-assign]
        OidcIdentity.objects.create(subject=subject, user=winner)
        return original(pk=None)

    OidcIdentity.objects.filter = _first_call_sees_nothing  # type: ignore[method-assign]
    try:
        resolved = _provision(subject, "raced")
    finally:
        OidcIdentity.objects.filter = original  # type: ignore[method-assign]

    # The winner's user, and exactly one identity for the subject — not a second account.
    assert resolved.pk == winner.pk
    assert OidcIdentity.objects.filter(subject=subject).count() == 1


def test_an_invited_account_is_claimed_on_first_login() -> None:
    """An account somebody created *for* this person is theirs the first time they arrive.

    The seed and the member picker both create accounts before their owner has ever signed in, so
    something has to recognise them by name once. That is what an invitation is
    (`apps.api.models.PendingIdentity`), and it is consumed by the claim.
    """
    from aira_management.apps.api.models import PendingIdentity

    invited = get_user_model().objects.create(username="newcomer")
    PendingIdentity.objects.create(user=invited, invited_by="boss")

    claimed = _provision("sub-newcomer", "newcomer")

    assert claimed.pk == invited.pk
    assert OidcIdentity.objects.get(subject="sub-newcomer").user_id == invited.pk
    assert not PendingIdentity.objects.filter(user=invited).exists(), (
        "an invitation that can be redeemed twice is not an invitation"
    )


def test_an_uninvited_account_is_never_claimed_by_a_matching_name() -> None:
    """The takeover this replaced (`FRD-613`).

    `_provision_user` used to adopt **any** unbound account whose username matched the token's
    `preferred_username`. Measured on 2026-08-30: a token with an arbitrary `sub` and
    `preferred_username: "admin"` was handed the seeded `admin` account, its memberships and its
    object permissions. Nothing had to be compromised and nothing recorded that it happened.
    """
    from aira_management.apps.api.models import PendingIdentity

    theirs = get_user_model().objects.create(username="admin")

    stranger = _provision("sub-stranger", "admin")

    assert stranger.pk != theirs.pk
    assert stranger.get_username() != "admin"
    assert not PendingIdentity.objects.exists()


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


def test_a_plaintext_identity_provider_refuses_to_start(monkeypatch) -> None:
    """Management verifies the same tokens against the same JWKS, so it needs the same refusal.
    Two planes, one rule (`aira_common.transport_security`) — a second copy is a second chance to
    forget one, which is the shape this project keeps recording."""
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    problems = unsafe_settings(
        ManagementSettings(
            environment="production",
            secret_key="a-real-secret-from-vault",
            allowed_hosts="aira.example.com",
            debug=False,
            role_groups="global-admin=/aira/global-admins",
            kafka_security_protocol="SASL_SSL",
            oidc_issuer="http://keycloak.example.com/realms/aira",
            oidc_audience="aira-management",
        )
    )

    assert any("AIRA_OIDC_ISSUER" in problem for problem in problems)


def test_a_plaintext_vault_address_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setenv("VAULT_ADDR", "http://vault.example:8200")

    problems = unsafe_settings(
        ManagementSettings(
            environment="production",
            secret_key="a-real-secret-from-vault",
            allowed_hosts="aira.example.com",
            debug=False,
            role_groups="global-admin=/aira/global-admins",
            kafka_security_protocol="SASL_SSL",
        )
    )

    assert any("VAULT_ADDR" in problem for problem in problems)
