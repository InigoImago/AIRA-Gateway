"""Roles as data, the Management half (`FRD-614` FR-3–FR-7, `ADR-0025`).

The group check is driven through `KeycloakDirectory` over an `httpx.MockTransport`, as the
directory's own tests drive it: a stand-in answering "exists" without Keycloak's shapes would be
more permissive than the thing it replaces.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from aira_management.apps.roles.models import RoleChange, StoredRole
from aira_management.apps.usecases import events
from aira_management.rbac import permissions_of, sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.directory import KeycloakDirectory
from aira_common.permissions import CATALOGUE, IT_STEUERUNG_DEFAULT, Permission

pytestmark = pytest.mark.django_db

BASE = "/api/v1/roles/"
GROUP_FOR = {
    "global-admin": "/aira/global-admins",
    "it-security": "/aira/it-security",
    "it-steuerung": "/aira/it-steuerung",
}
#: The groups the fake Keycloak has. Every other path is its 404.
EXISTING = {"/finance/controlling", "/finance/audit"}
BY_PATH = "/admin/realms/aira/group-by-path"


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Keycloak, reachable while ``state["up"]``."""
    state = {"up": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["up"]:
            return httpx.Response(503)
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token"})
        if request.url.path.startswith(BY_PATH):
            path = request.url.path[len(BY_PATH) :]
            if path in EXISTING:
                return httpx.Response(200, json={"path": path, "name": path.rsplit("/", 1)[-1]})
            return httpx.Response(404, json={"error": "Group path does not exist"})
        return httpx.Response(404)

    client = KeycloakDirectory(
        "https://keycloak.example",
        "aira",
        "aira-directory",
        "s3cret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("aira_management.apps.roles.views.build_directory", lambda: client)
    return state


@pytest.fixture
def captured() -> Any:
    seen: list[tuple[str, dict[str, Any]]] = []

    def spy(event_type: str, payload: dict[str, Any]) -> None:
        seen.append((event_type, payload))

    events.subscribe(spy)
    yield seen
    events.unsubscribe(spy)


def _user(name: str, *roles: str, groups: tuple[str, ...] = ()) -> Any:
    """Somebody whose token carries the groups of ``roles`` and ``groups`` — the only way either
    kind of role is held."""
    user = get_user_model().objects.create(username=name)
    claims = {"groups": [*(GROUP_FOR[role] for role in roles), *groups]}
    sync_user_roles(user, claims)
    sync_user_groups(user, claims)
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _admin() -> APIClient:
    return _client(_user("root", "global-admin"))


def _create(client: APIClient, **body: Any) -> Any:
    payload = {"label": "Controlling", "group_path": "/finance/controlling", "permissions": []}
    return client.post(BASE, {**payload, **body}, format="json")


# == creating ======================================================================================


def test_a_global_administrator_binds_a_role_to_a_group_keycloak_has(directory, captured) -> None:
    created = _create(_admin(), permissions=["report.read_all", "budget.installation.write"])

    assert created.status_code == 201, created.content
    body = created.json()
    assert body["slug"] == "controlling"
    assert body["group_paths"] == ["/finance/controlling"]
    # In the catalogue's order, whatever order they were ticked in.
    assert body["permissions"] == ["report.read_all", "budget.installation.write"]
    assert (body["builtin"], body["fixed"]) == (False, False)

    change = RoleChange.objects.get()
    assert (change.role_slug, change.action, change.actor) == ("controlling", "created", "root")
    assert change.before is None
    assert change.after["permissions"] == ["report.read_all", "budget.installation.write"]
    assert [name for name, _ in captured] == ["role.upserted"]
    assert captured[0][1]["group_path"] == "/finance/controlling"


def test_a_person_in_the_group_holds_exactly_what_the_role_grants(directory) -> None:
    _create(_admin(), permissions=["report.read_all"])

    member = _user("controller", groups=("/finance/controlling",))
    outsider = _user("neighbour", groups=("/finance/controlling-readonly",))

    assert permissions_of(member) == {Permission.REPORT_READ_ALL}
    assert permissions_of(outsider) == frozenset()


def test_a_group_keycloak_does_not_have_is_refused(directory) -> None:
    refused = _create(_admin(), group_path="/finance/nobody")

    assert refused.status_code == 400
    assert "/finance/nobody" in str(refused.json())
    assert not StoredRole.objects.filter(builtin=False).exists()


def test_a_group_nobody_could_check_is_refused_rather_than_trusted(directory, monkeypatch) -> None:
    directory["up"] = False
    assert _create(_admin()).status_code == 503

    monkeypatch.setattr("aira_management.apps.roles.views.build_directory", lambda: None)
    assert _create(_client(_user("root2", "global-admin"))).status_code == 503
    assert not StoredRole.objects.filter(builtin=False).exists()


def test_a_group_that_already_confers_a_role_is_refused(directory) -> None:
    admin = _admin()
    assert _create(admin, label="Security twin", group_path="/aira/it-security").status_code == 400
    assert _create(admin).status_code == 201
    assert _create(admin, label="Controlling twin").status_code == 400


def test_the_reserved_permission_and_an_unknown_one_are_refused(directory) -> None:
    admin = _admin()
    assert _create(admin, permissions=["role.manage"]).status_code == 400
    assert _create(admin, permissions=["no.such.thing"]).status_code == 400
    assert not StoredRole.objects.filter(builtin=False).exists()


def test_a_role_cannot_take_the_name_of_a_built_in_one(directory) -> None:
    assert _create(_admin(), label="IT Security", group_path="/finance/audit").status_code == 400


# == who may ======================================================================================


@pytest.mark.parametrize("role", ["it-security", "it-steuerung"])
def test_the_platform_roles_read_roles_and_change_none(directory, role) -> None:
    _create(_admin())
    reader = _client(_user(f"reader-{role}", role))

    assert reader.get(BASE).status_code == 200
    assert reader.get(f"{BASE}catalogue/").status_code == 200
    assert reader.get(f"{BASE}changes/").status_code == 200
    assert _create(reader, label="Another", group_path="/finance/audit").status_code == 403
    assert (
        reader.patch(f"{BASE}controlling/", {"permissions": []}, format="json").status_code == 403
    )
    assert reader.delete(f"{BASE}controlling/").status_code == 403


def test_somebody_without_role_read_is_not_shown_the_roles(directory) -> None:
    nobody = _client(_user("nobody"))
    assert nobody.get(BASE).status_code == 403
    assert nobody.get(f"{BASE}changes/").status_code == 403


@pytest.mark.parametrize("slug", ["global-admin", "it-security"])
def test_the_fixed_roles_cannot_be_changed_or_deleted(directory, slug) -> None:
    admin = _admin()
    assert admin.patch(f"{BASE}{slug}/", {"permissions": []}, format="json").status_code == 403
    assert admin.delete(f"{BASE}{slug}/").status_code == 403
    assert not RoleChange.objects.exists()


# == IT Steuerung =================================================================================


def test_it_steuerung_can_be_narrowed_and_the_narrowing_holds(directory, captured) -> None:
    steuerung = _user("itgov", "it-steuerung")
    assert permissions_of(steuerung) == IT_STEUERUNG_DEFAULT
    assert _client(steuerung).get("/api/v1/use-cases/retired/").status_code == 200

    narrowed = _admin().patch(
        f"{BASE}it-steuerung/", {"permissions": ["report.read_all"]}, format="json"
    )

    assert narrowed.status_code == 200, narrowed.content
    assert permissions_of(steuerung) == {Permission.REPORT_READ_ALL}
    assert _client(steuerung).get("/api/v1/use-cases/retired/").status_code == 403
    change = RoleChange.objects.get()
    assert set(change.before["permissions"]) == {str(p) for p in IT_STEUERUNG_DEFAULT}
    assert change.after["permissions"] == ["report.read_all"]
    assert captured[-1] == (
        "role.upserted",
        {
            "slug": "it-steuerung",
            "label": "IT Steuerung",
            "group_path": "",
            "permissions": ["report.read_all"],
            "builtin": True,
        },
    )


def test_it_steuerung_keeps_its_name_and_group_and_cannot_be_deleted(directory) -> None:
    admin = _admin()
    assert (
        admin.patch(f"{BASE}it-steuerung/", {"label": "Renamed"}, format="json").status_code == 400
    )
    assert (
        admin.patch(f"{BASE}it-steuerung/", {"group_path": "/finance/audit"}, format="json")
    ).status_code == 400
    assert admin.delete(f"{BASE}it-steuerung/").status_code == 403


# == changing =====================================================================================


def test_changes_take_effect_at_once_and_each_is_recorded(directory, captured) -> None:
    admin = _admin()
    _create(admin, permissions=["report.read_all"])
    first = _user("in-controlling", groups=("/finance/controlling",))
    second = _user("in-audit", groups=("/finance/audit",))

    admin.patch(
        f"{BASE}controlling/",
        {"permissions": ["report.read_all", "trace.read_all"]},
        format="json",
    )
    assert permissions_of(first) == {Permission.REPORT_READ_ALL, Permission.TRACE_READ_ALL}

    admin.patch(f"{BASE}controlling/", {"permissions": []}, format="json")
    assert permissions_of(first) == frozenset()

    admin.patch(
        f"{BASE}controlling/",
        {"group_path": "/finance/audit", "permissions": ["report.read_all"]},
        format="json",
    )
    assert permissions_of(first) == frozenset()
    assert permissions_of(second) == {Permission.REPORT_READ_ALL}

    assert admin.delete(f"{BASE}controlling/").status_code == 204
    assert permissions_of(second) == frozenset()

    assert list(RoleChange.objects.order_by("id").values_list("action", flat=True)) == [
        "created",
        "updated",
        "updated",
        "updated",
        "deleted",
    ]
    assert captured[-1] == ("role.removed", {"slug": "controlling"})


def test_rebinding_to_a_group_keycloak_does_not_have_is_refused(directory) -> None:
    admin = _admin()
    _create(admin)
    moved = admin.patch(f"{BASE}controlling/", {"group_path": "/finance/nobody"}, format="json")
    assert moved.status_code == 400
    assert StoredRole.objects.get(slug="controlling").group_path == "/finance/controlling"


def test_permissions_add_up_across_roles(directory) -> None:
    _create(_admin(), permissions=["budget.installation.write"])
    both = _user("both", "it-steuerung", groups=("/finance/controlling",))

    assert permissions_of(both) == IT_STEUERUNG_DEFAULT | {Permission.BUDGET_INSTALLATION_WRITE}


# == what the console reads =======================================================================


def test_the_catalogue_is_every_permission_in_order_with_the_reserved_one_marked(directory) -> None:
    rows = _admin().get(f"{BASE}catalogue/").json()

    assert [row["name"] for row in rows] == [str(permission) for permission in CATALOGUE]
    assert [row["name"] for row in rows if row["reserved"]] == ["role.manage"]


def test_the_list_shows_every_role_and_which_are_locked(directory) -> None:
    _create(_admin())
    roles = {row["slug"]: row for row in _second_admin().get(BASE).json()}

    assert set(roles) == {"global-admin", "it-security", "it-steuerung", "controlling"}
    assert [slug for slug, row in roles.items() if row["fixed"]] == ["global-admin", "it-security"]
    assert roles["global-admin"]["permissions"] == [str(p) for p in CATALOGUE]


def _second_admin() -> APIClient:
    return _client(_user("root-again", "global-admin"))


def test_me_names_a_stored_role_and_every_role_label(directory) -> None:
    _create(_admin(), permissions=["report.read_all"])
    me = _client(_user("controller", groups=("/finance/controlling",))).get("/api/v1/me").json()

    assert "controlling" in me["roles"]
    assert me["permissions"] == ["report.read_all"]
    assert me["role_labels"]["controlling"] == "Controlling"
    assert me["role_labels"]["global-admin"] == "Global administrator"


def test_administering_every_use_case_includes_seeing_them(directory) -> None:
    """A use case nobody can reach is one nobody can administer: `usecase.manage_all` alone must
    find the use case it may change, and `usecase.read_all` alone must not change it."""
    from aira_management.apps.usecases.models import UseCase

    UseCase.objects.create(slug="not-theirs", name="Not theirs")
    _create(_admin(), label="Operators", permissions=["usecase.manage_all"])
    _create(
        _second_admin(),
        label="Readers",
        group_path="/finance/audit",
        permissions=["usecase.read_all"],
    )
    operator = _client(_user("operator", groups=("/finance/controlling",)))
    reader = _client(_user("reader", groups=("/finance/audit",)))

    assert operator.get("/api/v1/use-cases/not-theirs/").status_code == 200
    changed = operator.patch("/api/v1/use-cases/not-theirs/", {"name": "Renamed"}, format="json")
    assert changed.status_code == 200, changed.content
    assert reader.get("/api/v1/use-cases/not-theirs/").status_code == 200
    refused = reader.patch("/api/v1/use-cases/not-theirs/", {"name": "Again"}, format="json")
    assert refused.status_code == 403
