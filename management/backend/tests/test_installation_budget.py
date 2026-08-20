"""The installation's own budget, and who may set it (`FRD-610`).

The residual bucket for spend that belongs to no use case — the console's model checks,
break-glass keys, demo traffic. Its own route rather than a use case's, because
`/use-cases/<slug>/budgets/` resolves an object from a slug this budget does not have, and bending
that route to accept an absent one makes *"which use case is this for"* a question with a special
answer at every layer that asks it.
"""

import pytest
from aira_management.apps.budgets.models import Budget
from aira_management.apps.usecases import events
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/installation-budgets/"
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


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"groups": [GROUP_FOR[role] for role in roles]})
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


# == who ==========================================================================================


def test_a_global_administrator_sets_it(captured_events) -> None:
    root = _client(_user("root", "global-admin"))

    created = root.post(BASE, {"period": "month", "limit_cost": "20.00"}, format="json")

    assert created.status_code == 201
    budget = Budget.objects.get(scope=Budget.INSTALLATION)
    assert budget.use_case is None
    # The event carries an **empty** use case, which is what selects the scope in the gateway.
    assert ("budget.upserted", budget.pk) in [
        (name, payload["id"]) for name, payload in captured_events
    ]
    assert next(p for _n, p in captured_events if p.get("id") == budget.pk)["use_case"] == ""


def test_governance_reads_it_and_does_not_set_it() -> None:
    """`ADR-0007`: `IT Steuerung` oversees and acts in nothing. The installation's own spend is
    exactly the figure a governance role is there to see — and setting it is an act."""
    _client(_user("root", "global-admin")).post(
        BASE, {"period": "month", "limit_cost": "20.00"}, format="json"
    )
    governance = _client(_user("gov", "it-steuerung"))

    listed = governance.get(BASE)
    assert listed.status_code == 200
    assert [row["scope"] for row in listed.json()] == [Budget.INSTALLATION]

    assert (
        governance.post(BASE, {"period": "day", "limit_cost": "1.00"}, format="json").status_code
        == 403
    )


def test_an_ordinary_member_sees_nothing() -> None:
    """Not a disclosure with a purpose: what the installation spends on its own diagnostics tells a
    use-case member nothing they can act on."""
    _client(_user("root", "global-admin")).post(
        BASE, {"period": "month", "limit_cost": "20.00"}, format="json"
    )

    assert _client(_user("nobody")).get(BASE).json() == []


# == what ========================================================================================


def test_it_upserts_on_the_period(captured_events) -> None:
    """A use-case budget upserts on `(scope, subject, period)`; here the scope is always
    `installation` and the subject always empty, so the period alone is the key."""
    root = _client(_user("root", "global-admin"))

    root.post(BASE, {"period": "month", "limit_cost": "20.00"}, format="json")
    root.post(BASE, {"period": "month", "limit_cost": "50.00"}, format="json")
    root.post(BASE, {"period": "day", "limit_cost": "5.00"}, format="json")

    assert Budget.objects.filter(scope=Budget.INSTALLATION).count() == 2
    assert (
        str(Budget.objects.get(scope=Budget.INSTALLATION, period="month").limit_cost) == "50.000000"
    )


def test_disabling_it_survives_the_next_edit() -> None:
    """The rule the use-case route learned the hard way: an upsert that does not mention `enabled`
    must not switch a deliberately disabled budget back on. A limit somebody lifted is a decision;
    reversing it silently is worse than never offering the switch."""
    root = _client(_user("root", "global-admin"))
    root.post(BASE, {"period": "month", "limit_cost": "20.00"}, format="json")
    root.post(BASE, {"period": "month", "limit_cost": "20.00", "enabled": False}, format="json")

    root.post(BASE, {"period": "month", "limit_cost": "30.00"}, format="json")

    assert Budget.objects.get(scope=Budget.INSTALLATION).enabled is False


def test_deleting_it_tells_the_gateway(captured_events) -> None:
    """Removing the row without saying so would leave the gateway enforcing a limit nobody can
    see — the shape `FRD-205` found once with API keys."""
    root = _client(_user("root", "global-admin"))
    created = root.post(BASE, {"period": "month", "limit_cost": "20.00"}, format="json").json()

    assert root.delete(f"{BASE}{created['id']}/").status_code == 204
    assert not Budget.objects.filter(scope=Budget.INSTALLATION).exists()
    assert ("budget.deleted", created["id"]) in [
        (name, payload["id"]) for name, payload in captured_events
    ]


# == the shapes the database refuses ==============================================================


def test_two_installation_budgets_for_one_period_are_refused() -> None:
    """**A NULL is not equal to itself in SQL**, so `uq_budget` stops policing the moment
    `use_case` may be null: two rows for one period would both be accepted and the gateway would
    enforce whichever it read first. A partial constraint covers exactly the rows the first cannot
    see — and this test is the reason to know it does."""
    Budget.objects.create(scope=Budget.INSTALLATION, use_case=None, subject="", period="month")

    with pytest.raises(IntegrityError):
        Budget.objects.create(scope=Budget.INSTALLATION, use_case=None, subject="", period="month")


def test_a_scope_and_an_owner_that_disagree_are_refused() -> None:
    """`clean()` runs for a form and not for a fixture, a shell or a migration, so the rule is in
    the database too. A row saying *installation* while naming a use case is a budget matched
    against traffic nobody meant."""
    from aira_management.apps.usecases.models import UseCase

    use_case = UseCase.objects.create(slug="uc", name="UC")

    with pytest.raises(IntegrityError):
        Budget.objects.create(
            scope=Budget.INSTALLATION, use_case=use_case, subject="", period="month"
        )
