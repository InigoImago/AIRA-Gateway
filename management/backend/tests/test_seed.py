from io import StringIO

import pytest
from aira_management.apps.seed.contributions.roles_and_users import (
    DEMO_USERS,
    seed_roles_and_users,
)
from aira_management.apps.seed.registry import contributions
from aira_management.config.app_settings import ManagementSettings
from aira_management.roles import Role
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def test_registry_contains_roles_and_users() -> None:
    names = [c.name for c in contributions()]
    assert "roles_and_users" in names
    # contributions are ordered
    orders = [c.order for c in contributions()]
    assert orders == sorted(orders)


def test_seed_creates_groups_and_users() -> None:
    summary = seed_roles_and_users(fresh=False)
    user_model = get_user_model()

    assert Group.objects.count() == len(Role)
    assert user_model.objects.count() == len(DEMO_USERS)
    assert summary["groups_created"] == len(Role)
    assert summary["users_created"] == len(DEMO_USERS)

    admin = user_model.objects.get(username="admin")
    assert admin.is_superuser and admin.is_staff
    assert admin.groups.filter(name=str(Role.GLOBAL_ADMIN)).exists()

    ucuser = user_model.objects.get(username="ucuser")
    assert not ucuser.is_superuser
    assert ucuser.groups.filter(name=str(Role.USE_CASE_USER)).exists()


def test_seed_is_idempotent() -> None:
    seed_roles_and_users(fresh=False)
    second = seed_roles_and_users(fresh=False)
    user_model = get_user_model()

    assert user_model.objects.count() == len(DEMO_USERS)
    assert Group.objects.count() == len(Role)
    assert second["users_created"] == 0
    assert second["groups_created"] == 0


def test_seed_fresh_removes_stray_demo_users() -> None:
    seed_roles_and_users(fresh=False)
    user_model = get_user_model()
    user_model.objects.create(username="stray", email="stray@demo.aira")

    seed_roles_and_users(fresh=True)
    assert not user_model.objects.filter(username="stray").exists()
    assert user_model.objects.count() == len(DEMO_USERS)


def test_command_runs() -> None:
    out = StringIO()
    call_command("seed_demo", stdout=out)
    output = out.getvalue()
    assert "roles_and_users" in output
    assert "done" in output
    assert get_user_model().objects.count() == len(DEMO_USERS)


def test_command_refuses_production_without_force(monkeypatch) -> None:
    from aira_management.apps.seed.management.commands import seed_demo as cmd

    monkeypatch.setattr(cmd, "get_settings", lambda: ManagementSettings(environment="production"))

    with pytest.raises(CommandError):
        call_command("seed_demo")

    # --force allows it
    call_command("seed_demo", "--force", stdout=StringIO())
    assert get_user_model().objects.count() == len(DEMO_USERS)
