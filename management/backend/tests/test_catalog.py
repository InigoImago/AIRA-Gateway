"""Model catalog with prices (FRD-403)."""

from decimal import Decimal

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.usecases import events
from aira_management.rbac import sync_user_roles
from aira_management.roles import Role
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/models/"


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
    """A spy on use-case events, removed again — **and only it**.

    This used to end with `events._subscribers.clear()`, which also removed the subscriber
    `OutboxConfig.ready()` registers at start-up. The list is module-global and start-up happens
    once, so from the first use of this fixture onwards **no use-case event reached the outbox for
    the rest of the session** — every later test asserting an outbox row was either testing nothing
    or failing for a reason a long way from its subject. Found by the second kind, in a command
    test that asserted a purge is announced.

    A teardown removes what it added. `clear()` is not a teardown, it is a reset of somebody else's
    state.
    """
    captured: list[tuple[str, dict]] = []
    spy = lambda kind, payload: captured.append((kind, payload))  # noqa: E731
    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


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
    # Every role that is **not** global-admin, plus somebody holding none — since `ADR-0017` the
    # last of those is what a use-case administrator looks like at the installation level.
    for role in ("it-steuerung", "it-security"):
        response = _client(_user(f"u-{role}", role)).post(BASE, PRICED, format="json")
        assert response.status_code == 403, role
    assert _client(_user("u-none")).post(BASE, PRICED, format="json").status_code == 403
    assert Model.objects.count() == 0


def test_any_authenticated_user_may_read_the_catalog() -> None:
    _client(_user("admin", "global-admin")).post(BASE, PRICED, format="json")
    response = _client(_user("reader")).get(BASE)
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


def test_who_may_catalogue_is_one_definition_both_planes_read() -> None:
    """The viewset's permission and the gateway's provider listing answer the same question, so
    they must not answer it separately.

    `FRD-503` is why this is a test rather than a convention: the gateway's kill switch was
    guarded by a *visibility* predicate while Management correctly refused the same person, and
    the two planes disagreed for as long as nobody thought to ask them together. Asking a vendor
    what its credential offers is only useful to somebody who may act on the answer — which is
    exactly this permission — and both ask `catalog.write` (`FRD-614`).
    """
    from aira_management.apps.catalog.views import ModelViewSet
    from aira_management.rbac import requires

    from aira_common.permissions import Permission

    view = ModelViewSet()
    view.action = "create"
    # The gateway's half is `test_provider_offerings.py`: its listing refuses without the same
    # permission, so both planes ask one question and no role-name predicate stands in for it.
    assert any(isinstance(p, requires(Permission.CATALOG_WRITE)) for p in view.get_permissions())


# ---- the KIRA id -----------------------------------------------------------------------


def test_a_model_gets_a_kira_id_it_can_be_addressed_by(captured_events) -> None:
    """**A model without one is invisible on half the product.**

    `numeric_id` is how a KIRA client names a model — that surface identifies models by integer,
    not by name. The field existed on the API and the console's form never offered it, so every
    model catalogued through the console got `NULL`: approvable, releasable, and unaddressable by
    `/kira/api/external/chat`, which answers `MODEL_NOT_FOUND` and names nothing that would tell a
    reader why. A control displayed as working and doing nothing (`FRD-125`).

    Assigned rather than demanded: a number nobody chose still beats no number.
    """
    response = _client(_user("boss", "global-admin")).post(
        BASE, {"name": "auto-id-1", "provider": "ollama"}, format="json"
    )

    assert response.status_code == 201, response.data
    assert isinstance(response.data["numeric_id"], int)
    assert response.data["numeric_id"] > 0


def test_a_chosen_kira_id_is_kept_because_a_migration_depends_on_it(captured_events) -> None:
    """The reason the field is settable at all (`FRD-107`).

    An installation moving off the predecessor has clients that already send particular ids. Giving
    AIRA's models exactly those numbers is what lets those clients keep working unchanged — so an
    id that was asked for must never be replaced by a generated one.
    """
    response = _client(_user("boss", "global-admin")).post(
        BASE, {"name": "chosen-id-1", "provider": "ollama", "numeric_id": 4711}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["numeric_id"] == 4711


def test_two_models_may_not_share_a_kira_id(captured_events) -> None:
    """The read side already refuses an ambiguous id — two entries claiming one make the surface
    answer 503 rather than guess which model to bill. This is the same rule where somebody can
    still fix it, and with a sentence rather than a database key name."""
    client = _client(_user("boss", "global-admin"))
    client.post(BASE, {"name": "first-1", "provider": "ollama", "numeric_id": 4712}, format="json")

    clash = client.post(
        BASE, {"name": "second-1", "provider": "ollama", "numeric_id": 4712}, format="json"
    )

    assert clash.status_code == 400
    said = str(clash.data)
    assert "4712" in said and "first-1" in said, said


def test_auto_assignment_does_not_collide_with_what_is_already_there(captured_events) -> None:
    """Generated ids climb past every existing one, chosen or generated. Handing out a number that
    is already taken would fail on the constraint — a 500 for the person cataloguing a model, on a
    field they never filled in."""
    client = _client(_user("boss", "global-admin"))
    client.post(BASE, {"name": "high-1", "provider": "ollama", "numeric_id": 99999}, format="json")

    generated = client.post(BASE, {"name": "next-1", "provider": "ollama"}, format="json")

    assert generated.status_code == 201, generated.data
    assert generated.data["numeric_id"] > 99999


def test_a_partial_price_edit_is_checked_against_the_model_it_will_produce() -> None:
    """The pair rule is about the **model**, not about the edit.

    `validate` asked `attrs.get(...)` for both prices, and a `PATCH` carries only what changed — so
    correcting one price on a fully priced model read as *"one price given, the other absent"* and
    was refused with a sentence telling the caller to set both, which they already had. Measured on
    2026-08-26.

    The declaration check five lines below it in the same method already merged over the instance
    and explains why. The rule was written down one layer down and not held one layer up.
    """
    client = _client(_user("ga", Role.GLOBAL_ADMIN))
    created = client.post(
        BASE,
        {
            "name": "priced-1",
            "input_price_per_million": "1.00",
            "output_price_per_million": "2.00",
        },
        format="json",
    )
    assert created.status_code in (200, 201), created.data

    response = client.patch(f"{BASE}priced-1/", {"input_price_per_million": "3.00"}, format="json")

    assert response.status_code == 200, response.data
    model = Model.objects.get(name="priced-1")
    assert model.input_price_per_million == Decimal("3.000000")
    assert model.output_price_per_million == Decimal("2.000000"), "the other price was not touched"


def test_clearing_one_price_of_a_pair_is_still_refused() -> None:
    """The half of the rule the fix must not lose. An explicit `null` **is** the resulting value,
    so a model left priced in one direction is refused exactly as it was before — reading the
    instance is about a field the caller did not mention, never about one they did."""
    client = _client(_user("ga", Role.GLOBAL_ADMIN))
    client.post(
        BASE,
        {
            "name": "priced-2",
            "input_price_per_million": "1.00",
            "output_price_per_million": "2.00",
        },
        format="json",
    )

    response = client.patch(f"{BASE}priced-2/", {"output_price_per_million": None}, format="json")

    assert response.status_code == 400
    assert Model.objects.get(name="priced-2").output_price_per_million == Decimal("2.000000")


# ---- a model's name is one the gateway can look up (`aira_common.model_names`) ---------------


@pytest.mark.parametrize("name", ["m\nX-Injected: 1", "tab\there", "bell\x07", "del\x7f"])
def test_a_name_with_a_control_character_is_refused_by_name(name: str) -> None:
    """The gateway refuses such a name as unlookupable, so an approved model by that name could
    never be served — and a line break in it reached the smoke-test export's
    `Content-Disposition`, where it raised `BadHeaderError` (a 500)."""
    response = _client(_user("ga-name", "global-admin")).post(BASE, {"name": name}, format="json")

    assert response.status_code == 400, response.content
    assert "control character" in str(response.json())
    assert not Model.objects.exists()


def test_the_name_rule_is_the_one_the_gateway_looks_up_by() -> None:
    """One rule, two planes: what Management lets somebody catalogue is what the gateway's catalogue
    can look up, in both directions."""
    from aira_common.model_names import MAX_MODEL_NAME, is_lookupable
    from aira_gateway import catalog as gateway_catalog

    assert Model._meta.get_field("name").max_length == MAX_MODEL_NAME
    assert gateway_catalog.MAX_MODEL_NAME == MAX_MODEL_NAME
    for name in ["gemini-2.5-flash", "a/b", "q:8b", "", "x" * 129, "a\nb", "a\x7fb", "ü"]:
        assert gateway_catalog.is_lookupable(name) is is_lookupable(name), name


SLASHED = "meta-llama/Llama-3.1-8B-Instruct"


def test_a_model_named_with_a_slash_can_be_read_changed_and_removed() -> None:
    """The ordinary name on an OpenAI-compatible server. It could be catalogued and then never
    addressed again: the console percent-encodes it, the server decodes `%2F` before routing, and
    the route matched one path segment — so Remove answered "not found" for ever."""
    from urllib.parse import quote

    client = _client(_user("ga-slash", "global-admin"))
    assert client.post(BASE, {"name": SLASHED}, format="json").status_code == 201
    # Exactly what `catalog.ts` builds with `encodeURIComponent`.
    path = f"{BASE}{quote(SLASHED, safe='')}/"

    assert client.get(path).json()["name"] == SLASHED
    assert client.patch(path, {"display_name": "Llama"}, format="json").status_code == 200
    assert client.delete(path).status_code == 204
    assert not Model.objects.filter(name=SLASHED).exists()


# ---- removing a model ends its releases on the gateway too (`FRD-308`) ------------------------


def test_removing_a_released_model_announces_the_use_cases_that_released_it(
    captured_events,
) -> None:
    """The release rows cascade here, and the gateway keeps its copy of each use case's list —
    so a name catalogued again later was callable there by every use case that once had it, while
    this console showed nothing released. Each affected **live** use case is announced again; a
    retired one is not, because an upsert after its retirement would bring it back."""
    from aira_management.apps.usecases.models import UseCase
    from django.utils import timezone

    client = _client(_user("ga-release", "global-admin"))
    client.post(BASE, {"name": "m-1", "approved": True}, format="json")
    live = UseCase.objects.create(slug="uc-live", name="Live")
    retired = UseCase.objects.create(slug="uc-gone", name="Gone", deleted_at=timezone.now())
    for usecase in (live, retired):
        usecase.allowed_models.set([Model.objects.get(name="m-1")])
    captured_events.clear()

    assert client.delete(f"{BASE}m-1/").status_code == 204

    assert ("model.deleted", {"name": "m-1"}) in captured_events
    announced = [p for kind, p in captured_events if kind == "usecase.upserted"]
    assert [p["slug"] for p in announced] == ["uc-live"]
    assert announced[0]["allowed_models"] == []


# ---- addressing is bounded configuration (`FRD-609`) -----------------------------------------


@pytest.mark.parametrize(
    ("addressing", "expected"),
    [
        ("europe-west4", "addressing must be an object"),
        ({"regions": "europe-west4"}, "addressing.regions must be a list"),
        ({"regions": ["europe-west4", ""]}, "addressing.regions must be a list"),
        ({"regions": [f"r{n}" for n in range(40)]}, "addressing.regions lists at most"),
        ({"regions": ["x" * 100]}, "at most 64 characters"),
        ({"deployment": "x" * 300}, "at most 256 characters"),
        ({"deployment": {"nested": "object"}}, "must be text or a list of text"),
        ({f"k{n}": "v" for n in range(20)}, "at most 8 keys"),
        ({"regions": ["eu"], "pad": "x" * 1_500_000}, "at most 256 characters"),
    ],
)
def test_addressing_is_refused_where_it_is_unbounded_or_misshapen(
    addressing: object, expected: str
) -> None:
    """`addressing` was stored as whatever arrived: three megabytes of it answered `201` and became
    an event larger than the bus carries. It stays open to a platform's own keys (`ADR-0011`), so
    it is bounded rather than closed: the regions as a list of names, every other value short."""
    response = _client(_user("ga-addr", "global-admin")).post(
        BASE, {"name": "addr-1", "addressing": addressing}, format="json"
    )

    assert response.status_code == 400, response.content
    assert expected in str(response.json())


def test_ordinary_addressing_is_accepted() -> None:
    """The console's shape, and a platform's own key beside it."""
    response = _client(_user("ga-addr-ok", "global-admin")).post(
        BASE,
        {"name": "addr-2", "addressing": {"regions": ["europe-west4", "eu"], "deployment": "p"}},
        format="json",
    )

    assert response.status_code == 201, response.content


# ---- the KIRA id is positive -----------------------------------------------------------------


def test_a_kira_id_of_zero_is_refused_by_name() -> None:
    """Dropping DRF's generated validators to name the clashing model also dropped the field's own
    range check, which `validate_numeric_id` restores — and nothing asserted that it does."""
    response = _client(_user("ga-zero-id", "global-admin")).post(
        BASE, {"name": "zero-id-1", "numeric_id": 0}, format="json"
    )

    assert response.status_code == 400, response.content
    assert "A KIRA id is a positive integer." in str(response.json())


# ---- two writers at once ---------------------------------------------------------------------


def test_an_insert_that_loses_a_race_is_a_conflict_with_a_sentence(monkeypatch) -> None:
    """Two creates read the same highest KIRA id, or both find no model of the name, and the second
    insert meets the unique constraint. Postgres serialises catalog writes with a lock; anything
    that still reaches the constraint is answered `409` with what to do, never a `500`."""
    from aira_management.apps.catalog import serializers as catalog_serializers
    from django.db import IntegrityError

    def clash(self, validated_data):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(catalog_serializers.ModelSerializer, "create", clash)

    response = _client(_user("ga-race", "global-admin")).post(
        BASE, {"name": "race-1"}, format="json"
    )

    assert response.status_code == 409, response.content
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert "try again" in body["error"]["message"].lower()


# ---- the price above a prompt length (FRD-408) --------------------------------------------

LONG = {
    **PRICED,
    "name": "gemini-2.5-pro",
    "input_price_per_million": "1.25",
    "output_price_per_million": "10",
    "long_context_threshold": 200000,
    "long_context_input_price_per_million": "2.5",
    "long_context_output_price_per_million": "15",
    "long_context_cached_input_price_per_million": "0.25",
}


def test_a_price_above_a_prompt_length_is_saved_and_published(captured_events) -> None:
    """Google bills a Gemini 2.5 Pro request whose prompt is longer than 200 000 tokens at a second
    set of rates, every token of it. The gateway can only charge what the event carries."""
    resp = _client(_user("admin", "global-admin")).post(BASE, LONG, format="json")

    assert resp.status_code == 201, resp.json()
    _, payload = captured_events[-1]
    assert payload["long_context_threshold"] == 200000
    assert payload["long_context_input_price_per_million"] == "2.500000"
    assert payload["long_context_output_price_per_million"] == "15.000000"
    assert payload["long_context_cached_input_price_per_million"] == "0.250000"
    assert payload["long_context_cache_write_price_per_million"] is None


@pytest.mark.parametrize(
    "missing",
    [
        "long_context_threshold",
        "long_context_input_price_per_million",
        "long_context_output_price_per_million",
    ],
)
def test_a_price_above_a_prompt_length_needs_its_length_and_both_directions(missing: str) -> None:
    """Each of the three alone would be read by the gateway as no second set at all — a price the
    console shows and nothing charges."""
    body = {key: value for key, value in LONG.items() if key != missing}

    resp = _client(_user("admin", "global-admin")).post(BASE, body, format="json")

    assert resp.status_code == 400
    assert "length" in str(resp.json())


def test_a_cache_price_above_a_length_without_the_length_is_refused() -> None:
    resp = _client(_user("admin", "global-admin")).post(
        BASE,
        {**PRICED, "long_context_cached_input_price_per_million": "0.25"},
        format="json",
    )

    assert resp.status_code == 400


def test_a_price_above_a_length_needs_the_ordinary_price_first() -> None:
    """A model priced only for long prompts would count every shorter one as unpriced."""
    body = {
        key: value
        for key, value in LONG.items()
        if key not in {"input_price_per_million", "output_price_per_million"}
    }

    resp = _client(_user("admin", "global-admin")).post(BASE, body, format="json")

    assert resp.status_code == 400
    assert "ordinary" in str(resp.json())


def test_clearing_the_length_alone_is_refused_on_an_edit() -> None:
    """Checked against the model the edit produces, as the ordinary pair is."""
    client = _client(_user("admin", "global-admin"))
    client.post(BASE, LONG, format="json")

    resp = client.patch(f"{BASE}gemini-2.5-pro/", {"long_context_threshold": None}, format="json")

    assert resp.status_code == 400
