"""Model catalog with prices (FRD-403)."""

from decimal import Decimal

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.usecases import events
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/models/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, {"realm_access": {"roles": list(roles)}})
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []
    events.subscribe(lambda t, p: captured.append((t, p)))
    yield captured
    events._subscribers.clear()


PRICED = {
    "name": "gemini-2.0-flash",
    "display_name": "Gemini 2.0 Flash",
    "provider": "google",
    "input_price_per_million": "0.075",
    "output_price_per_million": "0.30",
}


# ---- authorization ---------------------------------------------------------------------


def test_only_a_global_admin_may_set_prices() -> None:
    # Prices come from the provider contract, not from a use case — so they are maintained
    # centrally, and everyone else only reads them.
    for role in ("use-case-admin", "it-steuerung", "it-security"):
        response = _client(_user(f"u-{role}", role)).post(BASE, PRICED, format="json")
        assert response.status_code == 403, role
    assert Model.objects.count() == 0


def test_any_authenticated_user_may_read_the_catalog() -> None:
    _client(_user("admin", "global-admin")).post(BASE, PRICED, format="json")
    response = _client(_user("reader", "use-case-user")).get(BASE)
    assert response.status_code == 200
    assert response.json()[0]["name"] == "gemini-2.0-flash"


# ---- prices ----------------------------------------------------------------------------


def test_a_global_admin_prices_a_model() -> None:
    response = _client(_user("admin", "global-admin")).post(BASE, PRICED, format="json")
    assert response.status_code == 201

    model = Model.objects.get(name="gemini-2.0-flash")
    assert model.input_price_per_million == Decimal("0.075")
    assert model.output_price_per_million == Decimal("0.300000")
    assert model.is_priced is True


def test_a_model_may_be_catalogued_without_a_price() -> None:
    response = _client(_user("admin", "global-admin")).post(
        BASE, {"name": "unpriced-1"}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["is_priced"] is False


def test_pricing_only_one_direction_is_refused() -> None:
    # It would produce a cost figure that looks complete and silently omits the other half.
    response = _client(_user("admin", "global-admin")).post(
        BASE, {"name": "half-1", "input_price_per_million": "1.00"}, format="json"
    )
    assert response.status_code == 400
    assert "both" in str(response.json()).lower()


def test_posting_the_same_model_again_corrects_the_price() -> None:
    client = _client(_user("admin", "global-admin"))
    client.post(BASE, PRICED, format="json")
    response = client.post(BASE, {**PRICED, "input_price_per_million": "0.10"}, format="json")

    assert response.status_code == 200
    assert Model.objects.count() == 1
    assert Model.objects.get().input_price_per_million == Decimal("0.10")


def test_a_negative_price_is_refused() -> None:
    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {**PRICED, "input_price_per_million": "-1.00"},
        format="json",
    )
    assert response.status_code == 400


# ---- distribution ----------------------------------------------------------------------


def test_prices_are_published_as_exact_decimal_strings(captured_events) -> None:
    _client(_user("admin", "global-admin")).post(BASE, PRICED, format="json")

    event_type, payload = captured_events[-1]
    assert event_type == "model.upserted"
    # A JSON number would be a float by the time it reached the gateway.
    assert payload["input_price_per_million"] == "0.075000"
    assert isinstance(payload["input_price_per_million"], str)


def test_removing_a_model_is_published(captured_events) -> None:
    client = _client(_user("admin", "global-admin"))
    client.post(BASE, PRICED, format="json")
    response = client.delete(f"{BASE}gemini-2.0-flash/")

    assert response.status_code == 204
    assert captured_events[-1] == ("model.deleted", {"name": "gemini-2.0-flash"})


def test_an_unpriced_model_publishes_nulls(captured_events) -> None:
    _client(_user("admin", "global-admin")).post(BASE, {"name": "unpriced-1"}, format="json")
    _, payload = captured_events[-1]
    assert payload["input_price_per_million"] is None
    assert payload["output_price_per_million"] is None


def test_str_is_the_model_name() -> None:
    assert str(Model(name="m-1")) == "m-1"


def test_a_model_priced_only_on_output_is_refused_too() -> None:
    """The half-price rule has two directions and only one was tested.

    A model priced on output alone bills nothing for the prompt, so its figure looks complete and
    is short by whatever the input cost. That is the failure the rule exists to prevent, and it
    was undefended in exactly one of the two directions.
    """
    admin = _user("admin-out", "global-admin")
    resp = _client(admin).post(
        BASE,
        {"name": "output-only", "output_price_per_million": "0.30"},
        format="json",
    )
    assert resp.status_code == 400
    assert "both" in str(resp.json())


def test_a_model_priced_only_on_input_is_refused() -> None:
    admin = _user("admin-in", "global-admin")
    resp = _client(admin).post(
        BASE,
        {"name": "input-only", "input_price_per_million": "0.075"},
        format="json",
    )
    assert resp.status_code == 400
