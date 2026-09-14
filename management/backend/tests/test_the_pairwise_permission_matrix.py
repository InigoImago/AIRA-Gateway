"""Two groups, two roles: exactly the union, permission by permission (`FRD-614` FR-7).

The engine forms a union and nothing else. What could still escalate is a check that combines
permissions, so two of them together open something neither opens alone. So every pair of
grantable permissions is put on two roles bound to two groups, one person is put in both, and every
Management probe is asked. Each must answer exactly the pair plus what FR-11 documents, and `/me`
must list exactly the pair. The same holds for:

- a role holding one permission beside an empty one;
- two roles holding the same permission;
- a built-in role beside a custom one;
- a role beside a grant on a use case.

The gateway's half is `gateway/tests/test_the_pairwise_permission_matrix.py`.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.budgets.models import Budget
from aira_management.apps.roles.models import StoredRole
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views.grants import _grant
from aira_management.rbac import sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from aira_common.permissions import CATALOGUE, GRANTABLE, Permission, builtin_permissions
from aira_common.roles import Role

pytestmark = pytest.mark.django_db

P = Permission
GROUP_A, GROUP_B = "/pairs/a", "/pairs/b"
BUILTIN_GROUP = {
    Role.GLOBAL_ADMIN: "/aira/global-admins",
    Role.IT_SECURITY: "/aira/it-security",
    Role.IT_STEUERUNG: "/aira/it-steuerung",
}
ORDER = [permission for permission in CATALOGUE if permission in GRANTABLE]


@dataclass(frozen=True)
class Probe:
    permission: Permission
    what: str
    ask: Callable[[APIClient, dict[str, Any]], bool]
    #: Other permissions that open the same door, as FR-11 documents: administering every use
    #: case includes seeing them. Anything else a probe allows is an escalation.
    implied_by: frozenset[Permission] = frozenset()

    def allows(self, granted: frozenset[Permission]) -> bool:
        return self.permission in granted or bool(self.implied_by & granted)


def _verdict(response: Any, allowed: set[int], refused: set[int], what: str) -> bool:
    if response.status_code in allowed:
        return True
    if response.status_code in refused:
        return False
    raise AssertionError(f"{what}: unexpected {response.status_code} {response.content[:200]!r}")


def _call(method: str, path: str, allowed: set[int], refused: set[int], body: Any = None):
    def ask(client: APIClient, ctx: dict[str, Any]) -> bool:
        url = path.format(**ctx)
        if method in ("get", "delete"):
            response = getattr(client, method)(url)
        else:
            response = getattr(client, method)(url, body, format="json")
        return _verdict(response, allowed, refused, f"{method.upper()} {url}")

    return ask


def _lists_the_installation_budget(client: APIClient, ctx: dict[str, Any]) -> bool:  # noqa: ARG001
    """Answered to everybody, empty for whoever may not read every figure."""
    response = client.get("/api/v1/installation-budgets/")
    assert response.status_code == 200, response.content
    return bool(response.json())


PROBES: tuple[Probe, ...] = (
    Probe(
        P.USECASE_CREATE, "create a use case", _call("post", "/api/v1/use-cases/", {400}, {403}, {})
    ),
    Probe(
        P.USECASE_READ_ALL,
        "see a use case it holds no grant on",
        _call("get", "/api/v1/use-cases/probe/", {200}, {404}),
        frozenset({P.USECASE_MANAGE_ALL}),
    ),
    Probe(
        P.USECASE_MANAGE_ALL,
        "change a use case it holds no grant on",
        _call("patch", "/api/v1/use-cases/probe/", {200}, {403, 404}, {"name": "Probe"}),
    ),
    Probe(
        P.USECASE_READ_RETIRED,
        "list retired use cases",
        _call("get", "/api/v1/use-cases/retired/", {200}, {403}),
    ),
    Probe(
        P.USECASE_PURGE,
        "purge a retired use case (still waiting)",
        _call("delete", "/api/v1/use-cases/gone/purge/", {400}, {403}),
    ),
    Probe(P.CATALOG_WRITE, "declare a model", _call("post", "/api/v1/models/", {400}, {403}, {})),
    Probe(
        P.BUDGET_INSTALLATION_WRITE,
        "set the installation budget",
        _call("post", "/api/v1/installation-budgets/", {400}, {403}, {}),
    ),
    Probe(P.REPORT_READ_ALL, "read the installation budget", _lists_the_installation_budget),
    Probe(
        P.ANOMALY_RULE_GLOBAL_WRITE,
        "change a global anomaly rule",
        _call("patch", "/api/v1/anomaly-rules/{rule}/", {200}, {403}, {"enabled": False}),
    ),
    Probe(
        P.SMOKETEST_AUTHOR,
        "write a question",
        _call("post", "/api/v1/test-cases/", {400}, {403}, {}),
    ),
    Probe(
        P.SMOKETEST_RUN_ANY,
        "open the question catalogue",
        _call("get", "/api/v1/test-cases/", {200}, {403}),
    ),
    Probe(
        P.DIRECTORY_SEARCH,
        "search the directory",
        _call("get", "/api/v1/directory/?q=ab", {200}, {403}),
    ),
    Probe(P.ROLE_READ, "list the roles", _call("get", "/api/v1/roles/", {200}, {403})),
    Probe(
        P.ROLE_MANAGE,
        "check a group for binding",
        _call("post", "/api/v1/roles/check-group/", {400}, {403}, {"group_path": "/"}),
    ),
)

#: Permissions no Management endpoint decides, each probed by the gateway's half instead.
GATEWAY_ONLY = frozenset(
    {
        P.TRACE_READ_ALL,
        P.ANOMALY_READ_ALL,
        P.INCIDENT_SUSPEND,
        P.INCIDENT_INVESTIGATE,
        P.PAYLOAD_READ_ANY,
        P.CONTENT_READ_READ,
        P.OPERATIONS_DIAGNOSE,
    }
)


def _combinations() -> list[tuple[Permission, Permission | None]]:
    """Each permission beside an empty role, each held by both roles, and every pair."""
    alone = [(permission, None) for permission in ORDER]
    twice = [(permission, permission) for permission in ORDER]
    return alone + twice + list(itertools.combinations(ORDER, 2))


COMBINATIONS = _combinations()


def _id(pair: tuple[Permission, Permission | None]) -> str:
    return f"{pair[0]}+{pair[1] or 'nothing'}"


@pytest.fixture(autouse=True)
def _fresh_throttle() -> None:
    """Every combination is one person's few requests; the request throttle counts in the cache,
    which would otherwise carry hundreds of combinations into one minute."""
    cache.clear()


@pytest.fixture
def world() -> dict[str, Any]:
    """What the probes act on: a use case nobody holds a grant on, a retired one still inside its
    waiting period, a global rule, and an installation budget."""
    UseCase.objects.create(slug="probe", name="Probe")
    UseCase.objects.create(slug="gone", name="Gone", deleted_at=timezone.now() - timedelta(days=1))
    rule = AnomalyRule.objects.create(
        name="probe", kind="refusal_rate", window_minutes=15, threshold=40, enabled=False
    )
    Budget.objects.create(
        use_case=None, scope=Budget.INSTALLATION, period=Budget.MONTH, limit_cost=Decimal("10")
    )
    return {"rule": rule.pk}


def _bind(first: Permission, second: Permission | None) -> None:
    StoredRole.objects.create(
        slug="pair-a", label="Pair A", group_path=GROUP_A, permissions=[str(first)]
    )
    StoredRole.objects.create(
        slug="pair-b",
        label="Pair B",
        group_path=GROUP_B,
        permissions=[str(second)] if second else [],
    )


def _person(*groups: str) -> Any:
    """Somebody whose token carries ``groups`` — built-in role groups and custom ones alike."""
    user = get_user_model().objects.create(username="pair")
    claims = {"groups": list(groups)}
    sync_user_roles(user, claims)
    sync_user_groups(user, claims)
    return user


def _assert_exactly(
    user: Any,
    world: dict[str, Any],
    granted: frozenset[Permission],
    also: frozenset[Permission] = frozenset(),
) -> None:
    """`/me` lists exactly ``granted``; every probe opens exactly for ``granted`` and ``also``,
    where ``also`` is a door a grant on a use case opens without being a permission."""
    client = APIClient()
    client.force_authenticate(user=user)
    me = client.get("/api/v1/me").json()
    assert set(me["permissions"]) == {str(p) for p in granted}, "/me"
    wrong = [
        f"{probe.permission} — {probe.what}: {'allowed' if answer else 'refused'}"
        for probe in PROBES
        if (answer := probe.ask(client, world)) != probe.allows(granted | also)
    ]
    assert not wrong, f"with {sorted(granted)}: {wrong}"


def test_every_permission_is_probed_on_one_plane_or_the_other() -> None:
    assert {probe.permission for probe in PROBES} | GATEWAY_ONLY == set(Permission)
    assert not {probe.permission for probe in PROBES} & GATEWAY_ONLY


def test_the_combinations_are_every_single_every_overlap_and_every_pair() -> None:
    assert len(COMBINATIONS) == 2 * len(ORDER) + len(ORDER) * (len(ORDER) - 1) // 2


@pytest.mark.parametrize(("first", "second"), COMBINATIONS, ids=[_id(c) for c in COMBINATIONS])
def test_two_custom_roles_give_exactly_their_union(
    world: dict[str, Any], first: Permission, second: Permission | None
) -> None:
    _bind(first, second)
    granted = frozenset({first} | ({second} if second else set()))
    _assert_exactly(_person(GROUP_A, GROUP_B), world, granted)


@pytest.mark.parametrize(
    ("role", "extra"),
    [(role, extra) for role in Role for extra in ORDER],
    ids=[f"{role}+{extra}" for role in Role for extra in ORDER],
)
def test_a_builtin_role_and_a_custom_one_give_exactly_their_union(
    world: dict[str, Any], role: Role, extra: Permission
) -> None:
    _bind(extra, None)
    granted = builtin_permissions([str(role)]) | {extra}
    _assert_exactly(_person(BUILTIN_GROUP[role], GROUP_A), world, granted)


@pytest.mark.parametrize("grant", [UseCaseMembership.ADMIN, UseCaseMembership.USER])
@pytest.mark.parametrize("extra", ORDER, ids=str)
def test_a_grant_on_another_use_case_adds_no_permission(
    world: dict[str, Any], extra: Permission, grant: str
) -> None:
    """A grant is a relationship to one use case, never a permission. What an `admin` grant opens
    elsewhere is documented: the question catalogue's door and the directory, which serve the
    people who administer some use case (`FRD-504`, `FRD-209`). Nothing else."""
    _bind(extra, None)
    user = _person(GROUP_A)
    own = UseCase.objects.create(slug="own", name="Own")
    _grant(user, own, grant)
    UseCaseMembership.objects.create(use_case=own, user=user, role=grant)
    doors = (
        frozenset({P.SMOKETEST_RUN_ANY, P.DIRECTORY_SEARCH})
        if grant == UseCaseMembership.ADMIN
        else frozenset()
    )
    _assert_exactly(user, world, frozenset({extra}), also=doors)


def test_an_admin_grant_on_a_retired_use_case_opens_no_door(world: dict[str, Any]) -> None:
    """Retiring ends access at once (`FRD-607`). A person whose only admin grant is on a retired
    use case administers nothing, so the doors that serve administrators stay shut."""
    user = _person()
    retired = UseCase.objects.create(slug="was-mine", name="Was mine")
    _grant(user, retired, UseCaseMembership.ADMIN)
    UseCaseMembership.objects.create(use_case=retired, user=user, role=UseCaseMembership.ADMIN)
    retired.deleted_at = timezone.now()
    retired.save(update_fields=["deleted_at"])

    _assert_exactly(user, world, frozenset())
