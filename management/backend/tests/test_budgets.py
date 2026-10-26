"""Budget CRUD + distribution (FRD-400)."""

import pytest
from aira_management.apps.budgets.models import Budget
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
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


def _make_uc(admin, slug: str = "demo-uc") -> UseCase:
    _client(admin).post(BASE, {"slug": slug, "name": "Demo"}, format="json")
    return UseCase.objects.get(slug=slug)


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def test_create_use_case_budget_and_emit(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 100000},
        format="json",
    )
    assert resp.status_code == 201
    budget = Budget.objects.get(use_case__slug="demo-uc", scope="use_case")
    assert budget.limit_tokens == 100000
    published = [p for t, p in captured_events if t == "budget.upserted"]
    assert published[0]["use_case"] == "demo-uc"
    assert published[0]["id"] == budget.pk
    assert published[0]["limit_tokens"] == 100000


# == a person's own budget (`FRD-407`) ===========================================================


def _member(admin, slug: str, username: str, role: str = "user"):
    """``username`` as an account, given access to ``slug`` the way the console gives it."""
    user = get_user_model().objects.filter(username=username).first() or _user(username)
    resp = _client(admin).post(
        f"{BASE}{slug}/members/", {"username": username, "role": role}, format="json"
    )
    assert resp.status_code == 201, resp.content
    return user


def _own(client: APIClient, slug: str, subject: str, **limits: object):
    body = {"scope": "member", "subject": subject, "period": "month", **limits}
    return client.post(f"{BASE}{slug}/budgets/", body, format="json")


def test_a_persons_own_budget_is_stored_under_their_account_and_published(
    captured_events,
) -> None:
    """The name the gateway counts the person by is what is stored and sent — the account's own
    spelling, never the typed one, whitespace and all."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _member(admin, "demo-uc", "bob")

    resp = _own(_client(admin), "demo-uc", "  bob ", limit_cost="80.00")

    assert resp.status_code == 201, resp.content
    assert resp.json()["subject"] == "bob"
    assert resp.json()["reaches"] is True
    budget = Budget.objects.get(use_case__slug="demo-uc", scope="member")
    assert budget.subject == "bob"
    published = [payload for topic, payload in captured_events if topic == "budget.upserted"]
    assert (published[-1]["scope"], published[-1]["subject"]) == ("member", "bob")


def test_a_persons_own_budget_needs_a_name() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _own(_client(admin), "demo-uc", " ", limit_requests=5)
    assert resp.status_code == 400
    # Said as what to do, on the field it concerns — not as an account nobody could have.
    assert resp.json()["error"]["details"]["subject"] == [
        "Name the person this budget is for, by their username."
    ]
    assert not Budget.objects.filter(scope="member").exists()


def test_a_name_no_account_has_is_refused_by_name() -> None:
    """A budget for a name nobody has would bind nobody while looking configured."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _own(_client(admin), "demo-uc", "nobody", limit_requests=5)
    assert resp.status_code == 400
    assert "There is no user 'nobody'" in str(resp.json())
    assert not Budget.objects.filter(scope="member").exists()


def test_somebody_who_cannot_call_the_use_case_is_refused() -> None:
    """Their requests never reach the use case, so the budget would bound nothing — and a Global
    Administrator's blanket is not access to call it (`may_call`, the gateway's own rule)."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _user("outsider")
    other_admin = _user("admin2", "global-admin")

    for name in ("outsider", other_admin.username):
        resp = _own(_client(admin), "demo-uc", name, limit_requests=5)
        assert resp.status_code == 400, name
        assert "cannot call this use case" in str(resp.json()), name
    assert not Budget.objects.filter(scope="member").exists()


def test_a_plain_member_cannot_set_anybodys_own_budget() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    carol = _member(admin, "demo-uc", "carol")
    _member(admin, "demo-uc", "bob")
    assert _own(_client(carol), "demo-uc", "carol", limit_requests=99).status_code == 403
    assert _own(_client(carol), "demo-uc", "bob", limit_requests=1).status_code == 403


def test_each_person_has_one_own_budget_per_period() -> None:
    """Upserted on (scope, person, period): a second save for the same person and period replaces
    the first, another person or another period is another budget."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _member(admin, "demo-uc", "bob")
    _member(admin, "demo-uc", "carol")
    client = _client(admin)

    assert _own(client, "demo-uc", "bob", limit_requests=5).status_code == 201
    assert _own(client, "demo-uc", "bob", limit_requests=7).status_code == 201
    assert _own(client, "demo-uc", "carol", limit_requests=5).status_code == 201
    daily = {"scope": "member", "subject": "bob", "period": "day", "limit_requests": 1}
    assert client.post(f"{BASE}demo-uc/budgets/", daily, format="json").status_code == 201

    rows = sorted(
        Budget.objects.filter(scope="member").values_list("subject", "period", "limit_requests")
    )
    assert rows == [("bob", "day", 1), ("bob", "month", 7), ("carol", "month", 5)]


def test_the_database_holds_who_a_budget_names() -> None:
    """A fixture, a shell or a migration writes past the serializer: a person's own budget that
    names nobody, or a shared one that names somebody, is refused where it is stored."""
    from django.db import IntegrityError, transaction

    admin = _user("admin1", "global-admin")
    usecase = _make_uc(admin, "demo-uc")
    for scope, subject in (("member", ""), ("use_case", "bob"), ("each_member", "bob")):
        with pytest.raises(IntegrityError), transaction.atomic():
            Budget.objects.create(use_case=usecase, scope=scope, subject=subject, limit_requests=1)


def _rows_for(reader, slug: str = "demo-uc") -> dict[str, dict]:
    resp = _client(reader).get(f"{BASE}{slug}/budgets/")
    assert resp.status_code == 200, resp.content
    return {f"{row['scope']}#{row['id']}": row for row in resp.json()}


def _a_use_case_with_bobs_own_budget():
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _member(admin, "demo-uc", "bob")
    carol = _member(admin, "demo-uc", "carol")
    deputy = _member(admin, "demo-uc", "deputy", role="admin")
    assert _own(_client(admin), "demo-uc", "bob", limit_requests=3).status_code == 201
    budget = Budget.objects.get(scope="member")
    return admin, deputy, carol, budget


def test_under_strict_data_mode_the_name_is_for_those_who_set_it_and_the_person() -> None:
    """A colleague reads the row as another member's, as they read a key's owner (`FRD-627`
    FR-15): the limit stays visible, the person does not."""
    admin, deputy, carol, budget = _a_use_case_with_bobs_own_budget()
    bob = get_user_model().objects.get(username="bob")
    security = _user("sec1", "it-security")
    steering = _user("gov1", "it-steuerung")
    key = f"member#{budget.pk}"

    for reader in (admin, deputy, bob, security):
        row = _rows_for(reader)[key]
        assert (row["subject"], row["subject_withheld"]) == ("bob", False), reader.username
        assert row["reaches"] is True, reader.username
    for reader in (carol, steering):
        row = _rows_for(reader)[key]
        assert (row["subject"], row["subject_withheld"]) == (None, True), reader.username
        # Whether somebody withheld can still call is about that somebody too.
        assert row["reaches"] is None, reader.username
        assert row["limit_requests"] == 3, reader.username


def test_with_strict_data_mode_off_everybody_who_sees_the_use_case_reads_the_name(
    strict_data_off,
) -> None:
    """As member lists and key owners are read in that installation."""
    _admin, _deputy, carol, budget = _a_use_case_with_bobs_own_budget()
    row = _rows_for(carol)[f"member#{budget.pk}"]
    assert (row["subject"], row["subject_withheld"]) == ("bob", False)


def test_a_budget_for_somebody_who_left_says_so() -> None:
    """Removing a person ends their access and leaves their budget, which binds nobody now: the
    row says it reaches nobody rather than looking configured."""
    admin, _deputy, _carol, budget = _a_use_case_with_bobs_own_budget()
    assert _client(admin).delete(f"{BASE}demo-uc/members/bob/").status_code == 200
    assert _rows_for(admin)[f"member#{budget.pk}"]["reaches"] is False


def test_a_per_person_budget_names_nobody(captured_events) -> None:
    """The scope an administrator wants more often than either of the others: a fair share per
    head, with no list of heads to keep up to date.

    A subject sent with it is **dropped**, not refused — the row would still apply to everybody, so
    keeping the name would leave the configuration saying one thing and the enforcement doing
    another, and the gateway keys the counter on the caller either way.
    """
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "each_member", "subject": "bob", "period": "day", "limit_requests": 10},
        format="json",
    )
    assert resp.status_code == 201

    budget = Budget.objects.get(use_case__slug="demo-uc")
    assert (budget.scope, budget.subject) == ("each_member", "")
    published = [payload for topic, payload in captured_events if "budget" in topic]
    assert published[-1]["scope"] == "each_member"
    assert published[-1]["subject"] == ""


def test_budget_requires_a_limit() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month"},
        format="json",
    )
    assert resp.status_code == 400


def test_budget_rejects_negative_limit() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_requests": -5},
        format="json",
    )
    assert resp.status_code == 400


def test_post_upserts_same_scope_period() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    client = _client(admin)
    client.post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    client.post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 2000},
        format="json",
    )
    budgets = Budget.objects.filter(use_case__slug="demo-uc", scope="use_case", period="month")
    assert budgets.count() == 1
    assert budgets.first().limit_tokens == 2000


def test_member_may_read_budgets() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    assert _client(member).get(f"{BASE}demo-uc/budgets/").status_code == 200


def test_delete_budget_emits(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    created = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    budget_id = created.json()["id"]

    resp = _client(admin).delete(f"{BASE}demo-uc/budgets/{budget_id}/")
    assert resp.status_code == 204
    assert not Budget.objects.filter(pk=budget_id).exists()
    assert ("budget.deleted", {"id": budget_id, "use_case": "demo-uc"}) in captured_events


def test_delete_unknown_budget_is_400() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    assert _client(admin).delete(f"{BASE}demo-uc/budgets/999/").status_code == 400


def test_delete_budget_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    created = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).delete(f"{BASE}demo-uc/budgets/{created.json()['id']}/")
    assert resp.status_code == 403


def test_budget_write_forbidden_for_non_admin_member() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    member = _user("bob")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "bob", "role": "user"}, format="json"
    )
    resp = _client(member).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 1000},
        format="json",
    )
    assert resp.status_code == 403


def test_str_representation() -> None:
    admin = _user("admin1", "global-admin")
    uc = _make_uc(admin, "demo-uc")
    budget = Budget.objects.create(use_case=uc, scope="each_member", period="day")
    assert str(budget) == "budget each_member:demo-uc (day)"


# ---- cost limits (FRD-403) ---------------------------------------------------------------


def test_a_budget_may_cap_cost() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_cost": "250.00"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["limit_cost"] == "250.000000"


def test_a_budget_still_needs_at_least_one_limit() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/", {"scope": "use_case", "period": "month"}, format="json"
    )
    assert resp.status_code == 400
    assert "cost" in str(resp.json()).lower()


def test_the_cost_limit_is_published_as_a_string(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "day", "limit_cost": "12.50"},
        format="json",
    )
    published = [p for t, p in captured_events if t == "budget.upserted"]
    assert published[-1]["limit_cost"] == "12.500000"


def test_a_save_that_says_nothing_about_enabled_leaves_a_lifted_budget_lifted() -> None:
    """The console's own save never mentions `enabled`, and this used to switch it back on.

    Found while auditing which controls the console can reach: the upsert read
    `data.get("enabled", True)`, so changing a token cap on a budget somebody had deliberately
    switched off re-armed it — with nothing on screen saying so. A limit that was lifted is a
    decision, and silently reversing a decision is worse than never offering the switch.
    """
    admin = _user("enabled-admin", "global-admin")
    _make_uc(admin, "enabled-uc")
    client = _client(admin)
    client.post(
        f"{BASE}enabled-uc/budgets/",
        {"scope": "use_case", "period": "month", "limit_tokens": 100},
        format="json",
    )
    Budget.objects.filter(use_case__slug="enabled-uc").update(enabled=False)

    # Exactly the body `budgets-tab.ts` sends — no `enabled` key at all.
    resp = client.post(
        f"{BASE}enabled-uc/budgets/",
        {
            "scope": "use_case",
            "subject": "",
            "period": "month",
            "limit_cost": None,
            "limit_tokens": 200,
            "limit_requests": None,
        },
        format="json",
    )

    assert resp.status_code == 201
    budget = Budget.objects.get(use_case__slug="enabled-uc")
    assert budget.limit_tokens == 200, "the change itself must still apply"
    assert budget.enabled is False, "an upsert that says nothing must not re-arm the budget"


def test_enabled_is_honoured_when_it_is_said(captured_events) -> None:
    """And the switch itself works, in both directions, and reaches the gateway."""
    admin = _user("switch-admin", "global-admin")
    _make_uc(admin, "switch-uc")
    client = _client(admin)
    body = {"scope": "use_case", "subject": "", "period": "month", "limit_tokens": 50}

    client.post(f"{BASE}switch-uc/budgets/", body, format="json")
    client.post(f"{BASE}switch-uc/budgets/", {**body, "enabled": False}, format="json")
    assert Budget.objects.get(use_case__slug="switch-uc").enabled is False

    client.post(f"{BASE}switch-uc/budgets/", {**body, "enabled": True}, format="json")
    assert Budget.objects.get(use_case__slug="switch-uc").enabled is True

    # The gateway decides on its own copy, so the switch is only real if the event carries it.
    published = [p for t, p in captured_events if t == "budget.upserted"]
    assert [p["enabled"] for p in published[-3:]] == [True, False, True]


# ---- what a body may leave out, and what it may not say (`FRD-400` FR-2) ----------------------


def test_a_budget_without_a_period_is_a_monthly_one(captured_events) -> None:
    """`period` has a model default, so DRF made it optional and left it out of the validated data
    — and the upsert indexed it: `KeyError`, a `500`, for a body the field itself had accepted. An
    omitted period is the model's own default, and the event says which one it became."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/", {"scope": "use_case", "limit_cost": "10"}, format="json"
    )

    assert resp.status_code == 201, resp.content
    assert resp.json()["period"] == Budget.MONTH
    published = [p for t, p in captured_events if t == "budget.upserted"]
    assert published[-1]["period"] == Budget.MONTH


def test_an_installation_budget_is_not_set_on_a_use_case() -> None:
    """The scope belongs to the installation's own route (`FRD-610`). Accepted here, it reached the
    check constraint as an `IntegrityError` — a `500` naming nothing a caller could act on."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "installation", "period": "day", "limit_cost": "10"},
        format="json",
    )

    assert resp.status_code == 400, resp.content
    assert "installation-budgets" in str(resp.json())
    assert not Budget.objects.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("limit_tokens", 0), ("limit_requests", 0), ("limit_cost", "0"), ("limit_cost", "0.0000")],
)
def test_a_limit_of_zero_is_refused_by_name(field: str, value: object) -> None:
    """A limit of zero refuses every request — the gateway's ledger holds `usage >= limit` at
    zero — which is the accident the rate limit already refuses ("zero would mean refuse
    everything"). No limit is said by leaving the field empty, and a pause by switching the budget
    off."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/budgets/",
        {"scope": "use_case", "period": "month", field: value},
        format="json",
    )

    assert resp.status_code == 400, resp.content
    assert field in resp.json() or field in str(resp.json()), resp.json()
    assert not Budget.objects.exists()


def test_dropping_the_member_scope_cannot_be_rolled_back() -> None:
    """The migration says it is irreversible on purpose: a rollback cannot restore the rows it
    deleted. Its reverse was a no-op, so `migrate budgets 0003` reported a rollback that did not
    happen — Django raises `IrreversibleError` only for an operation that declares no reverse."""
    import importlib

    for module in (
        "aira_management.apps.budgets.migrations.0004_drop_member_scope",
        "aira_management.apps.ratelimits.migrations.0003_drop_member_scope",
    ):
        migration = importlib.import_module(module).Migration
        assert not all(operation.reversible for operation in migration.operations), module


@pytest.mark.django_db(transaction=True)
def test_the_upgrade_clears_a_name_left_on_a_shared_budget() -> None:
    """**The upgrade, run**: back to the state before `0006`, a per-head row carrying a name as an
    older writer could store one, then forwards. The name is cleared rather than the constraint
    refusing the deployment, and the row stays what it was."""
    from django.core.management import call_command
    from django.db import connection

    usecase = UseCase.objects.create(slug="older", name="Older")
    call_command("migrate", "budgets", "0005", verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO budgets_budget (use_case_id, scope, subject, period, limit_requests, "
            " enabled, created_at, updated_at) "
            "VALUES (%s, 'each_member', 'bob', 'month', 5, true, CURRENT_TIMESTAMP, "
            "        CURRENT_TIMESTAMP)",
            [usecase.pk],
        )

    call_command("migrate", "budgets", verbosity=0)

    assert list(Budget.objects.values_list("scope", "subject", "limit_requests")) == [
        ("each_member", "", 5)
    ]
