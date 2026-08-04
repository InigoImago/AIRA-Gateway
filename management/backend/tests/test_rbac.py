import types

import pytest
from aira_management.rbac import (
    IsGlobalAdmin,
    IsITSecurity,
    IsUseCaseAdmin,
    IsUseCaseUser,
    has_governance_role,
    has_role,
    scope_queryset,
    sync_user_roles,
)
from aira_management.roles import Role
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group

pytestmark = pytest.mark.django_db


def _user_with_roles(*roles: str):
    user = get_user_model().objects.create(username="u-" + ("-".join(roles) or "none"))
    sync_user_roles(user, {"realm_access": {"roles": list(roles)}})
    return user


def _request(user):
    return types.SimpleNamespace(user=user)


def test_sync_adds_and_removes_roles() -> None:
    user = get_user_model().objects.create(username="sync-user")
    sync_user_roles(user, {"realm_access": {"roles": ["global-admin", "it-security"]}})
    assert {"global-admin", "it-security"} <= set(user.groups.values_list("name", flat=True))

    sync_user_roles(user, {"realm_access": {"roles": ["it-security"]}})
    slugs = set(user.groups.values_list("name", flat=True))
    assert "global-admin" not in slugs
    assert "it-security" in slugs


def test_has_role_and_governance() -> None:
    admin = _user_with_roles("global-admin")
    assert has_role(admin, Role.GLOBAL_ADMIN) is True
    assert has_governance_role(admin) is True

    user = _user_with_roles("use-case-user")
    assert has_governance_role(user) is False


def test_has_role_anonymous_is_false() -> None:
    assert has_role(AnonymousUser(), Role.GLOBAL_ADMIN) is False


def test_permission_classes_global_admin_implies_all() -> None:
    admin = _request(_user_with_roles("global-admin"))
    assert IsGlobalAdmin().has_permission(admin, None) is True
    assert IsITSecurity().has_permission(admin, None) is True
    assert IsUseCaseAdmin().has_permission(admin, None) is True


def test_permission_class_denies_wrong_role() -> None:
    uc_user = _request(_user_with_roles("use-case-user"))
    assert IsUseCaseUser().has_permission(uc_user, None) is True
    assert IsUseCaseAdmin().has_permission(uc_user, None) is False
    assert IsGlobalAdmin().has_permission(uc_user, None) is False


def test_scope_queryset_governance_sees_all() -> None:
    admin = _user_with_roles("global-admin")
    scoped = scope_queryset(admin, "auth.view_group", Group.objects.all())
    assert scoped.count() == Group.objects.count()


def test_scope_queryset_non_governance_is_filtered() -> None:
    user = _user_with_roles("use-case-user")
    scoped = scope_queryset(user, "auth.view_group", Group.objects.all())
    assert scoped.count() == 0  # no object-level permissions granted
