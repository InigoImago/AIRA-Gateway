"""A use case's mail settings (`FRD-409`): set by its administrators, and carried to the gateway."""

from __future__ import annotations

import pytest
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
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


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


@pytest.fixture
def use_case_and_admin():
    owner = _user("owner", "global-admin")
    _client(owner).post(BASE, {"slug": "coding", "name": "Coding"}, format="json")
    usecase = UseCase.objects.get(slug="coding")
    admin = _user("ucadmin")
    _grant(admin, usecase, UseCaseMembership.ADMIN)
    UseCaseMembership.objects.create(use_case=usecase, user=admin, role=UseCaseMembership.ADMIN)
    return usecase, get_user_model().objects.get(pk=admin.pk)


def test_a_new_use_case_mails_when_a_budget_is_used_up_and_not_about_findings(
    use_case_and_admin,
) -> None:
    usecase, admin = use_case_and_admin
    body = _client(admin).get(f"{BASE}coding/").json()

    assert body["budget_mail_thresholds"] == [100]
    assert body["mail_admins_on_anomalies"] is False


def test_its_administrator_sets_both_and_the_gateway_hears_of_it(
    use_case_and_admin, captured_events
) -> None:
    usecase, admin = use_case_and_admin

    resp = _client(admin).patch(
        f"{BASE}coding/",
        {"budget_mail_thresholds": [100, 50, 80], "mail_admins_on_anomalies": True},
        format="json",
    )

    assert resp.status_code == 200, resp.content
    assert resp.json()["budget_mail_thresholds"] == [50, 80, 100]
    usecase.refresh_from_db()
    assert usecase.budget_mail_thresholds == [50, 80, 100]
    (upserted,) = [p for kind, p in captured_events if kind == "usecase.upserted"]
    assert upserted["budget_mail_thresholds"] == [50, 80, 100]
    assert upserted["mail_admins_on_anomalies"] is True


def test_no_thresholds_at_all_is_a_choice_the_gateway_hears_as_such(
    use_case_and_admin, captured_events
) -> None:
    usecase, admin = use_case_and_admin

    resp = _client(admin).patch(f"{BASE}coding/", {"budget_mail_thresholds": []}, format="json")

    assert resp.status_code == 200, resp.content
    (upserted,) = [p for kind, p in captured_events if kind == "usecase.upserted"]
    assert upserted["budget_mail_thresholds"] == []


@pytest.mark.parametrize(
    ("thresholds", "said"),
    [
        ([0], "from 1 to 100"),
        ([120], "from 1 to 100"),
        ([80, 80], "80 % is named twice"),
        ([10, 20, 30, 40, 50, 60], "At most 5"),
        ("80", "list of percentages"),
    ],
)
def test_thresholds_nobody_could_mean_are_refused_with_the_reason(
    use_case_and_admin, thresholds, said: str
) -> None:
    usecase, admin = use_case_and_admin

    resp = _client(admin).patch(
        f"{BASE}coding/", {"budget_mail_thresholds": thresholds}, format="json"
    )

    assert resp.status_code == 400
    assert said in str(resp.json())
    usecase.refresh_from_db()
    assert usecase.budget_mail_thresholds == [100]


def test_a_member_who_does_not_administer_it_cannot_change_them(use_case_and_admin) -> None:
    usecase, _ = use_case_and_admin
    member = _user("member")
    _grant(member, usecase, UseCaseMembership.USER)
    UseCaseMembership.objects.create(use_case=usecase, user=member, role=UseCaseMembership.USER)

    resp = _client(member).patch(
        f"{BASE}coding/", {"mail_admins_on_anomalies": True}, format="json"
    )

    assert resp.status_code == 403
    usecase.refresh_from_db()
    assert usecase.mail_admins_on_anomalies is False
