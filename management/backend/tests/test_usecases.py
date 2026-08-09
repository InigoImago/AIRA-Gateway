import pytest
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
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


def _create(client: APIClient, slug: str, name: str = "UC"):
    return client.post(BASE, {"slug": slug, "name": name}, format="json")


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


# ---- create -----------------------------------------------------------------------------


def test_create_as_use_case_admin() -> None:
    admin = _user("admin1", "use-case-admin")
    resp = _create(_client(admin), "my-uc")
    assert resp.status_code == 201
    assert resp.json()["slug"] == "my-uc"

    usecase = UseCase.objects.get(slug="my-uc")
    assert UseCaseMembership.objects.filter(use_case=usecase, user=admin, role="admin").exists()
    assert get_user_model().objects.get(pk=admin.pk).has_perm("usecases.change_usecase", usecase)


def test_create_forbidden_for_plain_user() -> None:
    user = _user("plain", "use-case-user")
    assert _create(_client(user), "x").status_code == 403


def test_invalid_slug_rejected() -> None:
    admin = _user("a", "use-case-admin")
    assert _create(_client(admin), "Bad_Slug").status_code == 400


def test_duplicate_slug_rejected() -> None:
    admin = _user("a", "use-case-admin")
    _create(_client(admin), "uc")
    assert _create(_client(admin), "uc").status_code == 400


# ---- visibility / scoping ---------------------------------------------------------------


def test_list_is_scoped_and_governance_sees_all() -> None:
    admin_a = _user("a", "use-case-admin")
    admin_b = _user("b", "use-case-admin")
    gov = _user("g", "global-admin")
    _create(_client(admin_a), "uc-a")
    _create(_client(admin_b), "uc-b")

    # The list is a **page** now (`FRD-208`): `results` plus a total. The total is part of the
    # answer, not decoration — a list that does not say how much it is not showing reads as
    # complete, which is how an installation with 801 use cases came to look like one with 25.
    a_page = _client(admin_a).get(BASE).json()
    assert {x["slug"] for x in a_page["results"]} == {"uc-a"}
    assert a_page["count"] == 1

    gov_page = _client(gov).get(BASE).json()
    assert {"uc-a", "uc-b"} <= {x["slug"] for x in gov_page["results"]}


def test_may_call_is_the_gateways_question_and_grants_a_global_admin_nothing() -> None:
    """**Three questions live near each other, and this is the third one.**

    What may I *see* is `scope_queryset`. What may I *administer here* is `is_member`, which grants
    a global administrator everything because in Management they may act anywhere. What may I
    *call* is neither: the gateway decides it from a token's groups and grants nobody a blanket.

    The first version of this filter reused `is_member`, and the console then offered a global
    admin the alphabetically first of nine hundred use cases. The gateway answered
    `Not a member of use case 'addr-1nn4ss'` on every question of a smoke-test run. This test is
    the one that would have caught it, and the one it replaced asserted agreement with `is_member`
    — the wrong reference, which locked the defect in rather than finding it.
    """
    admin = _user("a", "use-case-admin")
    _create(_client(admin), "uc-a")
    global_admin = _user("g", "global-admin")

    visible = _client(global_admin).get(BASE).json()
    callable_ = _client(global_admin).get(f"{BASE}?may_call=true").json()

    assert "uc-a" in {x["slug"] for x in visible["results"]}, "a global admin sees everything"
    assert callable_["results"] == [], (
        "and may call nothing: the gateway reads groups, and this token carries none"
    )


def test_may_call_follows_the_group_the_token_carries() -> None:
    """The rule is `aira_common.access.resolve` — the same function the gateway's grant resolver
    calls, rather than a second statement of it in Django. A slug decided twice is a slug that
    disagrees with itself eventually, and this pair of planes has paid for that before."""
    from aira_management.rbac import KEYCLOAK_GROUP_PREFIX
    from django.contrib.auth.models import Group

    member = _user("m", "use-case-user")
    _create(_client(_user("a", "use-case-admin")), "uc-a")
    _create(_client(_user("b", "use-case-admin")), "uc-b")
    group, _ = Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/uc-a")
    member.groups.add(group)

    listed = {x["slug"] for x in _client(member).get(f"{BASE}?may_call=true").json()["results"]}

    assert listed == {"uc-a"}


def test_may_call_answers_even_where_management_shows_nothing() -> None:
    """The `/use-cases/<slug>` convention grants **calling** without granting a guardian object
    permission, so a caller can be entitled to attribute traffic to a use case that
    `scope_queryset` does not show them. Filtering the visible set here would hand them an empty
    attribution list while the gateway accepted their requests — the same class of disagreement,
    pointing the other way."""
    from aira_management.rbac import KEYCLOAK_GROUP_PREFIX
    from django.contrib.auth.models import Group

    caller = _user("c", "use-case-user")
    _create(_client(_user("a", "use-case-admin")), "uc-a")
    group, _ = Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/uc-a")
    caller.groups.add(group)

    visible = _client(caller).get(BASE).json()["results"]
    callable_ = _client(caller).get(f"{BASE}?may_call=true").json()["results"]

    assert visible == [], "no object permission, so Management shows nothing"
    assert {x["slug"] for x in callable_} == {"uc-a"}


def test_the_list_is_searched_at_the_server() -> None:
    """Filtered by the database, not by the browser.

    The point of moving this off the client is that the rows a reader is not looking at are never
    built — and this serializer computes object-level permissions per row, which is the part that
    actually costs seconds.
    """
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    _create(client, "kundenservice")
    _create(client, "entwicklung")

    found = client.get(f"{BASE}?q=KUNDEN").json()
    assert {x["slug"] for x in found["results"]} == {"kundenservice"}
    assert found["count"] == 1

    # An empty needle is not a filter. Treating it as one would answer "nothing matches the empty
    # string", which is both wrong and the sort of emptiness a reader reads as a broken screen.
    assert client.get(f"{BASE}?q=%20%20").json()["count"] == 2


def test_a_page_is_a_page() -> None:
    """Bounded, ordered, and honest about the whole."""
    admin = _user("a", "use-case-admin")
    client = _client(admin)
    for index in range(7):
        _create(client, f"uc-{index}")

    first = client.get(f"{BASE}?page_size=3").json()
    assert len(first["results"]) == 3
    assert first["count"] == 7
    assert first["pages"] == 3

    second = client.get(f"{BASE}?page_size=3&page=2").json()
    # No row on both pages: an unordered queryset may hand the same row back twice and never show
    # a third, so the ordering is explicit rather than incidental.
    assert not {x["slug"] for x in first["results"]} & {x["slug"] for x in second["results"]}


def test_non_member_gets_404() -> None:
    admin = _user("a", "use-case-admin")
    outsider = _user("o")
    _create(_client(admin), "uc")
    assert _client(outsider).get(f"{BASE}uc/").status_code == 404


# ---- update / delete permissions --------------------------------------------------------


def test_admin_can_update_member_cannot() -> None:
    admin = _user("a", "use-case-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    assert _client(member).get(f"{BASE}uc/").status_code == 200
    assert _client(member).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 403
    assert _client(admin).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 200


def test_global_admin_can_update_any() -> None:
    owner = _user("o", "use-case-admin")
    gov = _user("g", "global-admin")
    _create(_client(owner), "uc")
    assert _client(gov).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 200


def test_admin_can_delete_member_cannot() -> None:
    admin = _user("a", "use-case-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    assert _client(member).delete(f"{BASE}uc/").status_code == 403
    assert _client(admin).delete(f"{BASE}uc/").status_code == 204
    assert not UseCase.objects.filter(slug="uc").exists()


# ---- membership -------------------------------------------------------------------------


def test_add_list_and_remove_member() -> None:
    admin = _user("a", "use-case-admin")
    member = _user("m")
    _create(_client(admin), "uc")

    added = _client(admin).post(
        f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json"
    )
    assert added.status_code == 201
    usecase = UseCase.objects.get(slug="uc")
    assert get_user_model().objects.get(pk=member.pk).has_perm("usecases.view_usecase", usecase)

    listed = _client(admin).get(f"{BASE}uc/members/").json()
    assert any(x["username"] == "m" for x in listed)
    assert _client(member).get(f"{BASE}uc/").status_code == 200

    assert _client(admin).delete(f"{BASE}uc/members/m/").status_code == 204
    assert _client(member).get(f"{BASE}uc/").status_code == 404


def test_member_cannot_add_members() -> None:
    admin = _user("a", "use-case-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    resp = _client(member).post(f"{BASE}uc/members/", {"username": "x"}, format="json")
    assert resp.status_code == 403


def test_add_unknown_user_returns_400() -> None:
    admin = _user("a", "use-case-admin")
    _create(_client(admin), "uc")
    resp = _client(admin).post(f"{BASE}uc/members/", {"username": "ghost"}, format="json")
    assert resp.status_code == 400


def test_member_cannot_remove_members() -> None:
    admin = _user("a", "use-case-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")
    assert _client(member).delete(f"{BASE}uc/members/m/").status_code == 403


def test_model_str() -> None:
    admin = _user("a", "use-case-admin")
    _create(_client(admin), "uc", name="My UC")
    usecase = UseCase.objects.get(slug="uc")
    assert str(usecase) == "uc"
    membership = usecase.memberships.get(user=admin)
    assert "admin" in str(membership)


def test_add_admin_member_grants_change() -> None:
    admin = _user("a", "use-case-admin")
    second = _user("s")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "s", "role": "admin"}, format="json")
    usecase = UseCase.objects.get(slug="uc")
    assert get_user_model().objects.get(pk=second.pk).has_perm("usecases.change_usecase", usecase)


# ---- change hook ------------------------------------------------------------------------


def test_change_hook_fires(captured_events) -> None:
    admin = _user("a", "use-case-admin")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "a", "role": "user"}, format="json")

    types = [t for t, _ in captured_events]
    assert "usecase.upserted" in types
    assert "membership.upserted" in types


def test_unsubscribe_not_present_is_noop() -> None:
    def never_subscribed(event_type: str, payload: dict) -> None:  # pragma: no cover
        return None

    events.unsubscribe(never_subscribed)  # no error, no-op


# ---- retention (FRD-404) -------------------------------------------------------------------


def test_a_new_use_case_keeps_payloads_for_a_week_by_default() -> None:
    admin = _user("admin1", "use-case-admin")
    resp = _create(_client(admin), "demo-uc", "Demo")
    assert resp.json()["retention_days"] == 7


def test_an_admin_can_shorten_or_extend_the_period() -> None:
    admin = _user("admin1", "use-case-admin")
    _create(_client(admin), "demo-uc", "Demo")

    resp = _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 1}, format="json")
    assert resp.status_code == 200
    assert resp.json()["retention_days"] == 1

    resp = _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 90}, format="json")
    assert resp.json()["retention_days"] == 90


def test_a_period_outside_the_allowed_range_is_refused() -> None:
    admin = _user("admin1", "use-case-admin")
    _create(_client(admin), "demo-uc", "Demo")
    # Zero would mean "delete immediately", which is a mistake, not a policy.
    client = _client(admin)
    for invalid in (0, 99999):
        response = client.patch(f"{BASE}demo-uc/", {"retention_days": invalid}, format="json")
        assert response.status_code == 400, invalid


def test_the_period_is_published_to_the_gateway(captured_events) -> None:
    admin = _user("admin1", "use-case-admin")
    _create(_client(admin), "demo-uc", "Demo")
    _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 14}, format="json")

    published = [p for t, p in captured_events if t == "usecase.upserted"]
    assert published[-1]["retention_days"] == 14


# ---- what the caller may do here, said out loud ------------------------------------------


def test_the_detail_says_what_this_caller_may_do() -> None:
    """Found by opening a use case as `ucuser` and seeing "Add member" and "Remove".

    Object-level permission lives in guardian rows, not in the token, so the console cannot work
    it out from `/me` — it was guessing, and it guessed generously. Clicking the button it offered
    produced a 403 from the screen that had just invited the click, which reads as a broken system
    rather than as a boundary.

    The answer is the same predicates that enforce it, returned on the object.
    """
    admin = _user("perm-admin", "use-case-admin")
    _create(_client(admin), "perm-uc")
    member = _user("perm-user", "use-case-user")
    _client(admin).post(
        f"{BASE}perm-uc/members/", {"username": "perm-user", "role": "user"}, format="json"
    )

    as_admin = _client(admin).get(f"{BASE}perm-uc/").json()["permissions"]
    assert as_admin == {"can_admin": True, "can_manage": True, "is_member": True}

    as_member = _client(member).get(f"{BASE}perm-uc/").json()["permissions"]
    assert as_member == {"can_admin": False, "can_manage": False, "is_member": True}


def test_seeing_every_use_case_is_not_being_in_one() -> None:
    """The other half, and the reason `is_member` is a separate answer from `can_manage`.

    An oversight role sees every use case (FRD-201/ADR-0007) and may act inside none of them —
    including issuing an API key, which is data-plane access. A console that read "I can see it"
    as "I belong to it" would put a key-issuing button in front of exactly the roles that must
    not have one.
    """
    admin = _user("scope-admin", "use-case-admin")
    _create(_client(admin), "scope-uc")
    steering = _user("scope-gov", "it-steuerung")

    permissions = _client(steering).get(f"{BASE}scope-uc/").json()["permissions"]

    assert permissions == {"can_admin": False, "can_manage": False, "is_member": False}


def test_the_permissions_a_use_case_reports_are_the_ones_it_enforces() -> None:
    """A restatement of a rule is a rule that drifts. This holds the two together: for each of the
    three answers, the corresponding request must agree with what the object said."""
    admin = _user("agree-admin", "use-case-admin")
    _create(_client(admin), "agree-uc")
    member = _user("agree-user", "use-case-user")
    _client(admin).post(
        f"{BASE}agree-uc/members/", {"username": "agree-user", "role": "user"}, format="json"
    )

    for user in (admin, member):
        client = _client(user)
        said = client.get(f"{BASE}agree-uc/").json()["permissions"]

        admin_allowed = client.patch(f"{BASE}agree-uc/", {"name": "x"}, format="json").status_code
        assert (admin_allowed != 403) is said["can_admin"]

        manage_allowed = client.post(
            f"{BASE}agree-uc/members/", {"username": "agree-user", "role": "user"}, format="json"
        ).status_code
        assert (manage_allowed != 403) is said["can_manage"]

        member_allowed = client.post(
            f"{BASE}agree-uc/api-keys/", {"label": "k"}, format="json"
        ).status_code
        assert (member_allowed != 403) is said["is_member"]
