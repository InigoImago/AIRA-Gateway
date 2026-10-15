import pytest
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import MAX_PROSE_LENGTH, UseCase, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims, setting

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    """A user holding organisation-wide ``roles``, via **group membership** (`ADR-0017`)."""
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _administrator(username: str, usecase: UseCase):
    """Somebody who administers **one** use case and holds no organisation-wide role.

    This is what `use-case-admin` used to approximate and never was: the realm role said "an
    administrator somewhere", the object grant says "an administrator *here*". Most of the cases
    below are about the second, and using the first as a stand-in is why a role that granted too
    much survived as long as it did.
    """
    user = _user(username)
    _grant(user, usecase, UseCaseMembership.ADMIN)
    UseCaseMembership.objects.create(use_case=usecase, user=user, role=UseCaseMembership.ADMIN)
    return get_user_model().objects.get(pk=user.pk)


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


def test_a_global_administrator_creates_one_and_administers_it() -> None:
    """Creating is `usecase.create` (`ADR-0017`); the creator becomes its administrator, by a
    membership like any other rather than by the role that let them create it."""
    admin = _user("admin1", "global-admin")
    resp = _create(_client(admin), "my-uc")
    assert resp.status_code == 201
    assert resp.json()["slug"] == "my-uc"

    usecase = UseCase.objects.get(slug="my-uc")
    assert UseCaseMembership.objects.filter(use_case=usecase, user=admin, role="admin").exists()
    assert get_user_model().objects.get(pk=admin.pk).has_perm("usecases.change_usecase", usecase)


def test_create_forbidden_for_plain_user() -> None:
    user = _user("plain")
    assert _create(_client(user), "x").status_code == 403


def test_invalid_slug_rejected() -> None:
    admin = _user("a", "global-admin")
    assert _create(_client(admin), "Bad_Slug").status_code == 400


def test_duplicate_slug_rejected() -> None:
    admin = _user("a", "global-admin")
    _create(_client(admin), "uc")
    assert _create(_client(admin), "uc").status_code == 400


# ---- visibility / scoping ---------------------------------------------------------------


def test_list_is_scoped_and_governance_sees_all() -> None:
    """**Rewritten for `ADR-0017`.** It used to scope by the `use-case-admin` realm role, which is
    gone; the creator is now a Global Administrator, and a Global Administrator sees everything —
    so scoping one *by that user* would have asserted nothing. The scoped caller is an
    administrator of one use case and nothing else, which is what the role always stood for.
    """
    creator = _user("creator", "global-admin")
    gov = _user("g", "it-steuerung")
    _create(_client(creator), "uc-a")
    _create(_client(creator), "uc-b")
    admin_a = _administrator("a", UseCase.objects.get(slug="uc-a"))

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
    admin = _user("a", "global-admin")
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

    member = _user("m")
    _create(_client(_user("a", "global-admin")), "uc-a")
    _create(_client(_user("b", "global-admin")), "uc-b")
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

    caller = _user("c")
    _create(_client(_user("a", "global-admin")), "uc-a")
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
    admin = _user("a", "global-admin")
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
    admin = _user("a", "global-admin")
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
    admin = _user("a", "global-admin")
    outsider = _user("o")
    _create(_client(admin), "uc")
    assert _client(outsider).get(f"{BASE}uc/").status_code == 404


# ---- update / delete permissions --------------------------------------------------------


def test_admin_can_update_member_cannot() -> None:
    admin = _user("a", "global-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    assert _client(member).get(f"{BASE}uc/").status_code == 200
    assert _client(member).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 403
    assert _client(admin).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 200


def test_global_admin_can_update_any() -> None:
    owner = _user("o", "global-admin")
    gov = _user("g", "global-admin")
    _create(_client(owner), "uc")
    assert _client(gov).patch(f"{BASE}uc/", {"name": "x"}, format="json").status_code == 200


def test_admin_can_delete_member_cannot() -> None:
    admin = _user("a", "global-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    assert _client(member).delete(f"{BASE}uc/").status_code == 403
    assert _client(admin).delete(f"{BASE}uc/").status_code == 204
    # **Retired, not removed** (`FRD-607`). This asserted the row was gone, which is precisely the
    # capability the owner asked to take away: the administrator of a compromised use case was the
    # one person able to destroy the record of what it had been configured to do.
    assert not UseCase.objects.filter(slug="uc", deleted_at__isnull=True).exists()
    retired = UseCase.objects.get(slug="uc")
    assert retired.deleted_at is not None
    assert retired.deleted_by == "a"


# ---- membership -------------------------------------------------------------------------


def test_add_list_and_remove_member() -> None:
    admin = _user("a", "global-admin")
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

    # `200` with the credentials the removal took away, not `204`: access ending revokes the keys
    # that rested on it (`FRD-613`), and a removal that silently revoked two of somebody's keys
    # would be a control whose effect the screen cannot state.
    removed = _client(admin).delete(f"{BASE}uc/members/m/")
    assert removed.status_code == 200
    assert removed.json() == {"revoked_keys": []}
    assert _client(member).get(f"{BASE}uc/").status_code == 404


def test_member_cannot_add_members() -> None:
    admin = _user("a", "global-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")

    resp = _client(member).post(f"{BASE}uc/members/", {"username": "x"}, format="json")
    assert resp.status_code == 403


def test_add_unknown_user_returns_400() -> None:
    admin = _user("a", "global-admin")
    _create(_client(admin), "uc")
    resp = _client(admin).post(f"{BASE}uc/members/", {"username": "ghost"}, format="json")
    assert resp.status_code == 400


def test_member_cannot_remove_members() -> None:
    admin = _user("a", "global-admin")
    member = _user("m")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "m", "role": "user"}, format="json")
    assert _client(member).delete(f"{BASE}uc/members/m/").status_code == 403


def test_model_str() -> None:
    admin = _user("a", "global-admin")
    _create(_client(admin), "uc", name="My UC")
    usecase = UseCase.objects.get(slug="uc")
    assert str(usecase) == "uc"
    membership = usecase.memberships.get(user=admin)
    assert "admin" in str(membership)


def test_add_admin_member_grants_change() -> None:
    admin = _user("a", "global-admin")
    second = _user("s")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "s", "role": "admin"}, format="json")
    usecase = UseCase.objects.get(slug="uc")
    assert get_user_model().objects.get(pk=second.pk).has_perm("usecases.change_usecase", usecase)


# ---- change hook ------------------------------------------------------------------------


def test_change_hook_fires(captured_events) -> None:
    admin = _user("a", "global-admin")
    _create(_client(admin), "uc")
    _client(admin).post(f"{BASE}uc/members/", {"username": "a", "role": "user"}, format="json")

    types = [t for t, _ in captured_events]
    assert "usecase.upserted" in types
    assert "membership.upserted" in types


def test_the_creator_is_announced_as_its_administrator(captured_events) -> None:
    """Creating a use case makes the creator its administrator (`ADR-0017`), and the gateway learns
    memberships only from these events. Stored and not announced, the creator is refused at the
    gateway as not a member of the use case they were just given."""
    admin = _user("a", "global-admin")
    _create(_client(admin), "made-here")

    announced = [p for t, p in captured_events if t == "membership.upserted"]
    assert announced == [{"slug": "made-here", "username": "a", "role": UseCaseMembership.ADMIN}]


def test_unsubscribe_not_present_is_noop() -> None:
    def never_subscribed(event_type: str, payload: dict) -> None:  # pragma: no cover
        return None

    events.unsubscribe(never_subscribed)  # no error, no-op


# ---- retention (FRD-404) -------------------------------------------------------------------


def test_a_new_use_case_keeps_payloads_for_a_week_by_default() -> None:
    admin = _user("admin1", "global-admin")
    resp = _create(_client(admin), "demo-uc", "Demo")
    assert resp.json()["retention_days"] == 7


def test_a_new_use_case_returns_reasoning_and_declares_no_functions() -> None:
    """Reasoning is on until an administrator turns it off (`FRD-135`). Tool calling stays off until
    one turns it on: only the use cases that need functions may declare them (`FRD-131`)."""
    admin = _user("admin1", "global-admin")
    body = _create(_client(admin), "demo-uc", "Demo").json()
    assert body["include_reasoning"] is True
    assert body["tools_enabled"] is False


def test_turning_reasoning_off_reaches_the_gateway(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    client = _client(admin)
    _create(client, "demo-uc", "Demo")

    resp = client.patch(f"{BASE}demo-uc/", {"include_reasoning": False}, format="json")

    assert resp.status_code == 200, resp.content
    assert UseCase.objects.get(slug="demo-uc").include_reasoning is False
    upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
    assert upserted[-1]["include_reasoning"] is False


def test_an_admin_can_shorten_or_extend_the_period() -> None:
    admin = _user("admin1", "global-admin")
    _create(_client(admin), "demo-uc", "Demo")

    resp = _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 1}, format="json")
    assert resp.status_code == 200
    assert resp.json()["retention_days"] == 1

    resp = _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 90}, format="json")
    assert resp.json()["retention_days"] == 90


def test_a_period_outside_the_allowed_range_is_refused() -> None:
    admin = _user("admin1", "global-admin")
    _create(_client(admin), "demo-uc", "Demo")
    # Zero would mean "delete immediately", which is a mistake, not a policy.
    client = _client(admin)
    for invalid in (0, 99999):
        response = client.patch(f"{BASE}demo-uc/", {"retention_days": invalid}, format="json")
        assert response.status_code == 400, invalid


def test_the_period_is_published_to_the_gateway(captured_events) -> None:
    admin = _user("admin1", "global-admin")
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
    admin = _user("perm-admin", "global-admin")
    _create(_client(admin), "perm-uc")
    member = _user("perm-user")
    _client(admin).post(
        f"{BASE}perm-uc/members/", {"username": "perm-user", "role": "user"}, format="json"
    )

    # `may_call` is the **gateway's** answer, and it is `True` here because both of them are named
    # by a grant: the creator as its administrator, the other by the membership just added. It is
    # still a fourth answer and not a synonym for `is_member` — a global administrator who is a
    # member of nothing may do everything on this screen and call nothing (`ADR-0007`), which
    # `test_seeing_every_use_case_is_not_being_in_one` holds and the group-grant suite pins from
    # both directions.
    as_admin = _client(admin).get(f"{BASE}perm-uc/").json()["permissions"]
    assert as_admin == {
        "can_admin": True,
        "can_manage": True,
        "is_member": True,
        "may_call": True,
    }

    as_member = _client(member).get(f"{BASE}perm-uc/").json()["permissions"]
    assert as_member == {
        "can_admin": False,
        "can_manage": False,
        "is_member": True,
        "may_call": True,
    }


def test_seeing_every_use_case_is_not_being_in_one() -> None:
    """The other half, and the reason `is_member` is a separate answer from `can_manage`.

    An oversight role sees every use case (FRD-201/ADR-0007) and may act inside none of them —
    including issuing an API key, which is data-plane access. A console that read "I can see it"
    as "I belong to it" would put a key-issuing button in front of exactly the roles that must
    not have one.
    """
    admin = _user("scope-admin", "global-admin")
    _create(_client(admin), "scope-uc")
    steering = _user("scope-gov", "it-steuerung")

    permissions = _client(steering).get(f"{BASE}scope-uc/").json()["permissions"]

    assert permissions == {
        "can_admin": False,
        "can_manage": False,
        "is_member": False,
        "may_call": False,
    }


def test_the_permissions_a_use_case_reports_are_the_ones_it_enforces() -> None:
    """A restatement of a rule is a rule that drifts. This holds the two together: for each of the
    three answers, the corresponding request must agree with what the object said."""
    admin = _user("agree-admin", "global-admin")
    _create(_client(admin), "agree-uc")
    member = _user("agree-user")
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


# ---- which models this use case may call (`FRD-308`) ------------------------------------


def _approved(name: str, approved: bool = True):
    from aira_management.apps.catalog.models import Model

    return Model.objects.create(name=name, approved=approved)


def test_an_administrator_of_the_use_case_releases_models_for_it(captured_events) -> None:
    """The owner's rule, 2026-08-11: *either a Global Administrator or a use-case administrator
    releases the allowed models for a use case*. The second half is the interesting one — it is a
    grant on that use case (`ADR-0017`), not an organisation-wide role."""
    _approved("gemini-2.5-flash")
    _approved("claude-sonnet-4-5")
    usecase = UseCase.objects.create(slug="uc", name="UC")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(
        f"{BASE}uc/", {"allowed_models": ["gemini-2.5-flash"]}, format="json"
    )

    assert response.status_code == 200, response.data
    assert response.json()["allowed_models"] == ["gemini-2.5-flash"]
    # And it travels, or the gateway enforces yesterday's decision.
    upserted = [p for t, p in captured_events if t == "usecase.upserted"]
    assert upserted[-1]["allowed_models"] == ["gemini-2.5-flash"]


def test_a_member_who_does_not_administer_it_cannot(captured_events) -> None:
    """Releasing a model is changing what the use case *is*, not working inside it."""
    _approved("gemini-2.5-flash")
    usecase = UseCase.objects.create(slug="uc", name="UC")
    member = _user("plain")
    # A member as the API makes one: the membership **and** its view grant, so the request reaches
    # the administration check instead of stopping at a `404` for a use case it cannot see.
    _grant(member, usecase, UseCaseMembership.USER)
    UseCaseMembership.objects.create(use_case=usecase, user=member, role=UseCaseMembership.USER)
    assert _client(member).get(f"{BASE}uc/").status_code == 200, "it sees the use case"

    response = _client(member).patch(
        f"{BASE}uc/", {"allowed_models": ["gemini-2.5-flash"]}, format="json"
    )

    assert response.status_code == 403
    assert list(UseCase.objects.get(slug="uc").allowed_models.all()) == []


def test_a_model_nobody_approved_cannot_be_released_and_is_named() -> None:
    """Two gates, two owners: a Global Administrator decides what may be used in this installation
    at all (`FRD-307`), a use-case administrator which of those this use case reaches. Letting the
    second hand out something the first withheld would invert them — and the request would be
    refused at dispatch anyway, so the console would be showing a release that never works.

    Named, because "one of the models you chose is not approved" sends somebody back through a
    list of thirty to work out which one."""
    _approved("released-1")
    _approved("draft-1", approved=False)
    UseCase.objects.create(slug="uc", name="UC")

    response = _client(_user("root", "global-admin")).patch(
        f"{BASE}uc/", {"allowed_models": ["released-1", "draft-1"]}, format="json"
    )

    assert response.status_code == 400
    # The unapproved one by name, and the approved one **not** blamed for it.
    assert "draft-1" in str(response.data)
    assert "released-1" not in str(response.data)
    assert list(UseCase.objects.get(slug="uc").allowed_models.all()) == []


def test_a_use_case_starts_with_no_model_released() -> None:
    """**Empty means none** (owner decision, 2026-08-11). The default is the decision: a use case
    reaches the models somebody chose for it, and a new one has had nobody choose yet."""
    response = _create(_client(_user("root", "global-admin")), "fresh")

    assert response.status_code == 201
    assert response.json()["allowed_models"] == []


def test_retiring_a_model_takes_it_out_of_every_release() -> None:
    """The question a relation was chosen for. With a list of names, "which use cases would break
    if I retire this" is a containment query written differently on SQLite and Postgres — the
    thing `FRD-505` already paid for once — and a deleted model would leave names behind that
    resolve to nothing."""
    model = _approved("retiring-1")
    usecase = UseCase.objects.create(slug="uc", name="UC")
    usecase.allowed_models.add(model)
    assert list(model.use_cases.all()) == [usecase]

    model.delete()

    assert list(UseCase.objects.get(slug="uc").allowed_models.all()) == []


def test_a_release_list_is_bounded() -> None:
    """The bound the `allow_check` step used to carry, kept when the step went. Every name is a
    database lookup, and an input nobody bounded is one somebody eventually sends ten thousand
    of (`ADR-0007`)."""
    UseCase.objects.create(slug="uc", name="UC")

    response = _client(_user("root", "global-admin")).patch(
        f"{BASE}uc/", {"allowed_models": [f"m-{i}" for i in range(200)]}, format="json"
    )

    assert response.status_code == 400


# ---- who may read the content, as one choice (`FRD-622` FR-1) ------------------------------


@pytest.mark.usefixtures("strict_data_off")
def test_a_use_case_starts_readable_by_its_members() -> None:
    """The default is the behaviour that existed before the setting did: a restriction is imposed,
    never assumed. Asserted on a freshly created use case, because a default nobody states is a
    default somebody discovers."""
    admin = _user("admin1", "global-admin")
    created = _create(_client(admin), "demo-uc", "Demo")

    assert created.json()["payload_visibility"] == "members"
    assert UseCase.objects.get(slug="demo-uc").payload_visibility == "members"


@pytest.mark.usefixtures("strict_data_off")
@pytest.mark.parametrize("visibility", ["members", "own", "oversight"])
def test_an_administrator_of_the_use_case_chooses_who_may_read_it(
    captured_events, visibility: str
) -> None:
    """All three, through the API that saves them, by somebody who holds **no** organisation-wide
    role — this is a grant on the use case (`ADR-0017`), and the panel belongs to whoever
    administers it.

    `oversight` included on purpose: it takes the administrator's *own* reading away, and they are
    allowed to choose it and allowed to change their mind (the owner's decision, 2026-10-11).
    """
    usecase = UseCase.objects.create(slug="uc", name="UC")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(f"{BASE}uc/", {"payload_visibility": visibility}, format="json")

    assert response.status_code == 200, response.data
    assert response.json()["payload_visibility"] == visibility
    assert UseCase.objects.get(slug="uc").payload_visibility == visibility
    # And it travels, or the gateway enforces yesterday's decision.
    upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
    assert upserted[-1]["payload_visibility"] == visibility


@pytest.mark.usefixtures("strict_data_off")
def test_the_oversight_mode_can_be_left_again() -> None:
    """The door closes and opens. Making it one-way was considered and declined: the panel belongs
    to the use case's administrator, and a latch only an installation role could open would be a
    second kind of ownership on one setting (owner's decision)."""
    usecase = UseCase.objects.create(slug="uc", name="UC", payload_visibility="oversight")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(f"{BASE}uc/", {"payload_visibility": "members"}, format="json")

    assert response.status_code == 200, response.data
    assert UseCase.objects.get(slug="uc").payload_visibility == "members"


@pytest.mark.parametrize(
    "value", ["", "everyone", "oversight ", "OVERSIGHT", True, 3, None, "own,members"]
)
def test_a_visibility_nobody_defined_is_refused_where_it_is_typed(value: object) -> None:
    """The gateway reads an unknown value as the **narrowest** of the three, deliberately — so a
    typo here would silently hide a use case's content from its own members rather than fail.
    Which is exactly why it has to be refused at the boundary where somebody can still see what
    they typed (`ADR-0018`: both ends, each for its own reason)."""
    admin = _user("admin1", "global-admin")
    _create(_client(admin), "demo-uc", "Demo")

    response = _client(admin).patch(f"{BASE}demo-uc/", {"payload_visibility": value}, format="json")

    assert response.status_code == 400, f"{value!r} was accepted"


@pytest.mark.usefixtures("strict_data_off")
def test_the_event_still_carries_the_retired_boolean_for_a_gateway_that_reads_it(
    captured_events,
) -> None:
    """**The upgrade window, which is a day long and real** (`FRD-204`): Management is upgraded
    first, and a gateway that has not been yet reads `restrict_members_to_own_requests` and nothing
    else. Dropping it from the event would *unrestrict* every restricted use case on those
    instances — a disclosure arriving as an upgrade — so it is still emitted, derived, and
    anything that is not `members` is a restriction as far as that gateway is concerned.
    """
    admin = _user("admin1", "global-admin")
    _create(_client(admin), "demo-uc", "Demo")
    client = _client(admin)

    sent: dict[str, dict] = {}
    for visibility in ("members", "own", "oversight"):
        client.patch(f"{BASE}demo-uc/", {"payload_visibility": visibility}, format="json")
        upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
        sent[visibility] = upserted[-1]

    assert sent["members"]["restrict_members_to_own_requests"] is False
    assert sent["own"]["restrict_members_to_own_requests"] is True
    # Oversight is narrower than the boolean can say, so it says the narrowest it can.
    assert sent["oversight"]["restrict_members_to_own_requests"] is True
    assert sent["oversight"]["payload_visibility"] == "oversight"


def test_under_strict_data_mode_the_retired_boolean_always_restricts(captured_events) -> None:
    """A gateway too old to read `payload_visibility` knows no data mode either, and reads only the
    boolean. Under the mode every use case is `oversight` (`FRD-627`), so the boolean restricts,
    whatever the use case stored — which is `members` for one created without the field."""
    admin = _user("admin1", "global-admin")
    _create(_client(admin), "demo-uc", "Demo")
    _client(admin).patch(f"{BASE}demo-uc/", {"retention_days": 9}, format="json")

    upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
    assert upserted[-1]["restrict_members_to_own_requests"] is True
    assert UseCase.objects.get(slug="demo-uc").payload_visibility == "members"


def test_a_reader_is_refused_by_who_they_are_before_what_they_sent() -> None:
    """IT Steuerung reads every use case and administers none. Validating its body first told it
    to correct a field it may not set at all, and refused it only once the values were valid."""
    root = _user("root", "global-admin")
    _create(_client(root), "uc")
    reader = _user("reader", "it-steuerung")

    invalid = _client(reader).patch(f"{BASE}uc/", {"retention_days": 0}, format="json")
    valid = _client(reader).patch(f"{BASE}uc/", {"retention_days": 30}, format="json")

    assert invalid.status_code == 403, invalid.content
    assert valid.status_code == 403
    assert UseCase.objects.get(slug="uc").retention_days != 30


@pytest.mark.parametrize("field", ["description", "processing_notes"])
def test_prose_is_bounded_where_it_is_written(field: str) -> None:
    """Both travel in every `usecase.upserted`, and the event channel carries about a megabyte:
    unbounded, one save could produce an event the relay can never send. Refused by name."""
    root = _user("root", "global-admin")
    _create(_client(root), "uc")

    too_long = _client(root).patch(
        f"{BASE}uc/", {field: "€" * (MAX_PROSE_LENGTH + 1)}, format="json"
    )
    at_the_bound = _client(root).patch(f"{BASE}uc/", {field: "€" * MAX_PROSE_LENGTH}, format="json")

    assert too_long.status_code == 400
    assert field in too_long.json()["error"]["details"]
    assert at_the_bound.status_code == 200, at_the_bound.content


def _queries_for_the_list(rows: int) -> int:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    member = _user(f"member-{rows}")
    for index in range(rows):
        usecase = UseCase.objects.create(slug=f"q{rows}-{index:02}", name=f"UC {index}")
        _grant(member, usecase, UseCaseMembership.USER)
        UseCaseMembership.objects.create(use_case=usecase, user=member, role=UseCaseMembership.USER)
    client = _client(get_user_model().objects.get(pk=member.pk))
    with CaptureQueriesContext(connection) as captured:
        response = client.get(BASE)
    assert response.status_code == 200
    assert response.json()["count"] == rows
    return len(captured.captured_queries)


def test_a_page_of_use_cases_costs_the_same_however_many_rows_it_has() -> None:
    """Each row says what this caller may do there, and every predicate re-read the caller's
    roles, groups, grants and memberships: about sixteen queries a row, four hundred for a page of
    twenty-five. They are the request's answers now (`aira_management.memo`), and the object
    permissions and releases are read for the page at once."""
    one_row = _queries_for_the_list(1)  # first, so it pays for the process-wide caches
    assert _queries_for_the_list(25) == one_row


# ---- strict data mode decides content visibility for every use case (`FRD-627` FR-3) ----------

#: The message a refusal must carry: it names the mode, so a legitimate need finds the operator.
STRICT_MODE_NAMED = "AIRA_STRICT_DATA_MODE"


def test_a_new_use_case_is_created_and_reported_as_oversight_in_strict_mode() -> None:
    """Creation does not fail on the column's default; what is stored stays the default, and what
    is answered is what the gateway enforces."""
    admin = _user("admin1", "global-admin")
    created = _create(_client(admin), "demo-uc", "Demo")

    assert created.status_code == 201, created.data
    assert created.json()["payload_visibility"] == "oversight"
    assert UseCase.objects.get(slug="demo-uc").payload_visibility == "members"


@pytest.mark.parametrize("visibility", ["members", "own"])
def test_strict_mode_refuses_saving_a_visibility_that_lets_the_use_case_read(
    captured_events, visibility: str
) -> None:
    usecase = UseCase.objects.create(slug="uc", name="UC", payload_visibility="oversight")
    admin = _administrator("uc-admin", usecase)
    before = len(captured_events)

    response = _client(admin).patch(f"{BASE}uc/", {"payload_visibility": visibility}, format="json")

    assert response.status_code == 400
    assert STRICT_MODE_NAMED in str(response.data)
    assert UseCase.objects.get(slug="uc").payload_visibility == "oversight"
    assert len(captured_events) == before, "a refused save announced a change"


@pytest.mark.parametrize("visibility", ["members", "own"])
def test_strict_mode_refuses_creating_a_use_case_its_members_could_read(visibility: str) -> None:
    admin = _user("admin1", "global-admin")

    response = _client(admin).post(
        BASE, {"slug": "uc", "name": "UC", "payload_visibility": visibility}, format="json"
    )

    assert response.status_code == 400
    assert STRICT_MODE_NAMED in str(response.data)
    assert not UseCase.objects.filter(slug="uc").exists()


def test_strict_mode_accepts_oversight_and_leaves_other_fields_writable(captured_events) -> None:
    """A screen that sends back what it was shown must not be refused for it."""
    usecase = UseCase.objects.create(slug="uc", name="UC")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(
        f"{BASE}uc/", {"payload_visibility": "oversight", "name": "Renamed"}, format="json"
    )

    assert response.status_code == 200, response.data
    assert UseCase.objects.get(slug="uc").payload_visibility == "oversight"
    upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
    assert upserted[-1]["payload_visibility"] == "oversight"


@pytest.mark.parametrize("stored", ["members", "own"])
def test_strict_mode_reports_the_effective_visibility_and_keeps_the_stored_one(
    captured_events, stored: str
) -> None:
    """Decided on read (`ADR-0036`): the answer is `oversight`, the row and the event keep the
    administrator's choice, and turning the mode off answers it again — lossless."""
    usecase = UseCase.objects.create(slug="uc", name="UC", payload_visibility=stored)
    admin = _administrator("uc-admin", usecase)
    client = _client(admin)

    assert client.get(f"{BASE}uc/").json()["payload_visibility"] == "oversight"
    listed = client.get(BASE).json()["results"]
    assert [row["payload_visibility"] for row in listed] == ["oversight"]

    renamed = client.patch(f"{BASE}uc/", {"name": "Renamed"}, format="json")
    assert renamed.json()["payload_visibility"] == "oversight"
    assert UseCase.objects.get(slug="uc").payload_visibility == stored
    upserted = [payload for kind, payload in captured_events if kind == "usecase.upserted"]
    assert upserted[-1]["payload_visibility"] == stored, "the gateway applies the mode itself"

    with setting(strict_data_mode=False):
        assert client.get(f"{BASE}uc/").json()["payload_visibility"] == stored
