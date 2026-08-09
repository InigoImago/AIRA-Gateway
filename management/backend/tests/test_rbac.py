import types

import pytest
from aira_management.rbac import (
    IsGlobalAdmin,
    IsGlobalAdminOrUseCaseAdministrator,
    IsITSecurity,
    has_governance_role,
    has_oversight_role,
    has_role,
    scope_queryset,
    sync_user_roles,
)
from aira_management.roles import Role
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group

pytestmark = pytest.mark.django_db

#: What an installation configures (`ADR-0017`). The tests read the same string a deployment sets,
#: so a case cannot pass against a mapping the parser would refuse.
ROLE_GROUPS = (
    "global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung"
)
GROUP_FOR = {
    "global-admin": "/aira/global-admins",
    "it-security": "/aira/it-security",
    "it-steuerung": "/aira/it-steuerung",
}


@pytest.fixture(autouse=True)
def _role_groups(settings):
    settings.AIRA_ROLE_GROUPS = ROLE_GROUPS


def _user_with_roles(*roles: str):
    """A user holding ``roles``, granted the only way a role can now be held: **group
    membership**. There is deliberately no path here for `use-case-admin` or `use-case-user` —
    those are grants on one use case, not properties of a person (`ADR-0017`)."""
    user = get_user_model().objects.create(username="u-" + ("-".join(roles) or "none"))
    sync_user_roles(user, {"groups": [GROUP_FOR[role] for role in roles]})
    return user


def _request(user):
    return types.SimpleNamespace(user=user)


def test_sync_adds_and_removes_roles() -> None:
    user = get_user_model().objects.create(username="sync-user")
    sync_user_roles(user, {"groups": ["/aira/global-admins", "/aira/it-security"]})
    assert {"global-admin", "it-security"} <= set(user.groups.values_list("name", flat=True))

    sync_user_roles(user, {"groups": ["/aira/it-security"]})
    slugs = set(user.groups.values_list("name", flat=True))
    assert "global-admin" not in slugs
    assert "it-security" in slugs


def test_a_realm_role_confers_nothing(settings) -> None:
    """**The guarantee `ADR-0017` exists for.** A role is held through a group and through nothing
    else, so an administrator who assigns the realm role directly in Keycloak has granted nothing.
    Asserted by sending exactly that token — reading the code would only show that the claim is
    unused, which is not the same as showing it cannot grant."""
    user = get_user_model().objects.create(username="realm-role-user")

    sync_user_roles(user, {"realm_access": {"roles": ["global-admin", "it-security"]}})

    assert set(user.groups.values_list("name", flat=True)) == set()
    assert has_role(user, Role.GLOBAL_ADMIN) is False


def test_a_group_the_configuration_does_not_name_confers_nothing() -> None:
    user = get_user_model().objects.create(username="other-group-user")

    sync_user_roles(user, {"groups": ["/some/other/group", "/use-cases/demo-uc"]})

    assert set(user.groups.values_list("name", flat=True)) == set()


def test_a_malformed_groups_claim_confers_nothing_rather_than_raising() -> None:
    """A realm misconfiguration must stop *authority*, not authentication — a caller whose claim
    is the wrong shape gets no role, and still gets a session in which the console can say so."""
    user = get_user_model().objects.create(username="malformed-user")

    sync_user_roles(user, {"groups": "not-a-list"})

    assert set(user.groups.values_list("name", flat=True)) == set()


def test_has_role_and_governance() -> None:
    admin = _user_with_roles("global-admin")
    assert has_role(admin, Role.GLOBAL_ADMIN) is True
    assert has_governance_role(admin) is True

    user = _user_with_roles()
    assert has_governance_role(user) is False


def test_has_role_anonymous_is_false() -> None:
    assert has_role(AnonymousUser(), Role.GLOBAL_ADMIN) is False


def test_permission_classes_global_admin_implies_all() -> None:
    admin = _request(_user_with_roles("global-admin"))
    assert IsGlobalAdmin().has_permission(admin, None) is True
    assert IsITSecurity().has_permission(admin, None) is True
    assert IsGlobalAdminOrUseCaseAdministrator().has_permission(admin, None) is True


def test_permission_class_denies_somebody_with_no_role() -> None:
    """Somebody in none of the configured groups. Under `ADR-0017` that is what a use-case user
    now *is* at the organisation level: their authority is on a use case, not on the installation,
    so the role gates are all shut for them."""
    nobody = _request(_user_with_roles())
    assert IsGlobalAdminOrUseCaseAdministrator().has_permission(nobody, None) is False
    assert IsGlobalAdmin().has_permission(nobody, None) is False
    assert IsITSecurity().has_permission(nobody, None) is False


def test_scope_queryset_governance_sees_all() -> None:
    admin = _user_with_roles("global-admin")
    scoped = scope_queryset(admin, "auth.view_group", Group.objects.all())
    assert scoped.count() == Group.objects.count()


def test_scope_queryset_non_governance_is_filtered() -> None:
    user = _user_with_roles()
    scoped = scope_queryset(user, "auth.view_group", Group.objects.all())
    assert scoped.count() == 0  # no object-level permissions granted


@pytest.mark.django_db
def test_security_oversight_is_a_restricted_view_not_an_absent_one() -> None:
    """Found by logging in as `itsec` and finding an empty console.

    PRD §154 gives IT Security "security oversight (restricted view) … cross-use-case anomaly
    visibility … **cannot** see all business content by default". The restriction is on content and
    spend, not on knowing which use cases exist — retention, payload storage, filters and limits
    are exactly the security-relevant metadata that role is there to oversee.

    It was folded in with the spend roles, so it saw nothing at all. A role that sees nothing is
    not a restricted view.
    """
    from aira_management.apps.usecases.models import UseCase

    UseCase.objects.create(slug="somebody-elses", name="Not theirs")
    user = get_user_model().objects.create(username="itsec-probe")
    sync_user_roles(user, {"groups": ["/aira/it-security"]})

    visible = scope_queryset(user, "usecases.view_usecase", UseCase.objects.all())

    assert visible.filter(slug="somebody-elses").exists()


@pytest.mark.django_db
def test_security_oversight_is_still_not_a_spend_role() -> None:
    """The other half, and the reason the two sets are separate rather than one widened set: the
    figures stay with the roles the PRD gives them."""
    user = get_user_model().objects.create(username="itsec-spend")
    sync_user_roles(user, {"groups": ["/aira/it-security"]})

    assert not has_governance_role(user)
    assert has_oversight_role(user)
