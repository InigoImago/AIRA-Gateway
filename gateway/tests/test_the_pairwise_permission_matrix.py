"""The gateway's half of the pairwise permission matrix (`FRD-614` FR-7).

Every pair of grantable permissions is put on two roles in the gateway's read model. A caller whose
token carries both groups is resolved through `RoleResolver` and `_with_permissions`, as a real
request is, and every gateway probe is asked. Each must answer exactly the pair, and nothing two
permissions open together that neither opens alone. The same holds for a built-in role beside a
custom one, and for a role beside a grant on another use case.

The Management half is `management/backend/tests/test_the_pairwise_permission_matrix.py`.
"""

from __future__ import annotations

import itertools
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_common.permissions import CATALOGUE, GRANTABLE, Permission, builtin_permissions
from aira_common.roles import Role
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import _with_permissions, require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.auth.role_definitions import RoleResolver
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import HANDLERS
from aira_gateway.db.models import RequestLog, UseCaseRead

P = Permission
MAPPING = {
    Role.GLOBAL_ADMIN: ("/aira/global-admins",),
    Role.IT_SECURITY: ("/aira/it-security",),
    Role.IT_STEUERUNG: ("/aira/it-steuerung",),
}
GROUP_A, GROUP_B = "/pairs/a", "/pairs/b"
ORDER = [permission for permission in CATALOGUE if permission in GRANTABLE]


@dataclass(frozen=True)
class Probe:
    permission: Permission
    what: str
    ask: Callable[[TestClient], bool]


def _status(path: str, allowed: set[int], refused: set[int]) -> Callable[[TestClient], bool]:
    def ask(client: TestClient) -> bool:
        response = client.get(path)
        if response.status_code in allowed:
            return True
        if response.status_code in refused:
            return False
        raise AssertionError(f"GET {path}: unexpected {response.status_code} {response.text[:200]}")

    return ask


def _refuses_body(path: str) -> Callable[[TestClient], bool]:
    """A write the permission opens: an empty body is the caller's mistake (400) once past the
    permission check, and a 403 before it. Nothing is created either way."""

    def ask(client: TestClient) -> bool:
        response = client.post(path, json={})
        if response.status_code == 400:
            return True
        if response.status_code == 403:
            return False
        raise AssertionError(
            f"POST {path}: unexpected {response.status_code} {response.text[:200]}"
        )

    return ask


def _in_scope(path: str) -> Callable[[TestClient], bool]:
    """A list that answers an empty 200 outside the caller's scope, and says so."""

    def ask(client: TestClient) -> bool:
        response = client.get(path)
        assert response.status_code == 200, f"GET {path}: {response.text}"
        return bool(response.json().get("in_scope"))

    return ask


PROBES: tuple[Probe, ...] = (
    Probe(P.CATALOG_WRITE, "list what providers offer", _status("/v1beta/providers", {200}, {403})),
    Probe(
        P.REPORT_READ_ALL,
        "read a use case's usage it is no member of",
        _status("/v1beta/usage/probe", {200}, {403}),
    ),
    Probe(
        P.TRACE_READ_ALL, "list a use case's requests", _in_scope("/v1beta/traces?use_case=probe")
    ),
    Probe(
        P.ANOMALY_READ_ALL,
        "list a use case's findings",
        _in_scope("/v1beta/anomalies?use_case=probe"),
    ),
    # Stopping, not listing: every stop is also listed to `anomaly.read_all`, whose findings already
    # say what was done (`FRD-503` FR-8).
    Probe(P.INCIDENT_SUSPEND, "stop traffic", _refuses_body("/v1beta/suspensions")),
    Probe(
        P.INCIDENT_INVESTIGATE,
        "filter requests by source address",
        _status("/v1beta/traces?source_ip=192.0.2.1", {200}, {403}),
    ),
    Probe(
        P.PAYLOAD_READ_ANY,
        "open a request's stored content",
        _status("/v1beta/traces/row-1/payload", {200}, {403, 404}),
    ),
    Probe(
        P.CONTENT_READ_READ,
        "read the content-read log",
        _status("/v1beta/content-reads", {200}, {403}),
    ),
    Probe(
        P.OPERATIONS_DIAGNOSE,
        "check a model",
        _status("/v1beta/models/matrix-probe:check", {200}, {403}),
    ),
)

#: Permissions no gateway endpoint decides, each probed by the Management half instead.
MANAGEMENT_ONLY = frozenset(
    {
        P.USECASE_CREATE,
        P.USECASE_READ_ALL,
        P.USECASE_MANAGE_ALL,
        P.USECASE_READ_RETIRED,
        P.USECASE_PURGE,
        P.BUDGET_INSTALLATION_WRITE,
        P.ANOMALY_RULE_GLOBAL_WRITE,
        P.SMOKETEST_AUTHOR,
        P.SMOKETEST_RUN_ANY,
        P.DIRECTORY_SEARCH,
        P.ROLE_READ,
        P.ROLE_MANAGE,
    }
)


def _combinations() -> list[tuple[Permission, Permission | None]]:
    alone = [(permission, None) for permission in ORDER]
    twice = [(permission, permission) for permission in ORDER]
    return alone + twice + list(itertools.combinations(ORDER, 2))


COMBINATIONS = _combinations()


def _id(pair: tuple[Permission, Permission | None]) -> str:
    return f"{pair[0]}+{pair[1] or 'nothing'}"


async def _seed(app: Any) -> None:
    """A use case the caller holds no grant on, with one stored request in it."""
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="probe", name="Probe", store_payloads=True))
        session.add(
            RequestLog(
                id="row-1",
                subject="alice",
                auth_method="api_key",
                use_case="probe",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                created_at=datetime.now(UTC),
                request_payload={"contents": [{"parts": [{"text": "hallo"}]}]},
                response_payload={"candidates": []},
            )
        )
        await session.commit()


@pytest.fixture(scope="module")
def gateway() -> Iterator[tuple[TestClient, Any]]:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        client.portal.call(_seed, app)
        yield client, app
    app.dependency_overrides.clear()


async def _bind(app: Any, first: Permission, second: Permission | None) -> None:
    """The two roles, as `aira.roles` delivers them: the consumer's own handler."""
    async with app.state.db_sessionmaker() as session:
        for slug, group, held in (("pair-a", GROUP_A, [first]), ("pair-b", GROUP_B, [second])):
            permissions = [str(permission) for permission in held if permission]
            await HANDLERS["role.upserted"](
                session,
                {
                    "slug": slug,
                    "label": slug,
                    "group_path": group,
                    "permissions": permissions,
                    "builtin": False,
                },
            )
        await session.commit()


async def _resolve(app: Any, caller: Principal) -> Principal:
    """What a real request does one layer out of the token: a fresh resolver, so no cache stands
    between the read model and the answer."""
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                role_definitions=RoleResolver(app.state.db_sessionmaker, MAPPING)
            )
        )
    )
    return await _with_permissions(request, caller)  # type: ignore[arg-type]


def _assert_exactly(
    gateway: tuple[TestClient, Any],
    first: Permission,
    second: Permission | None,
    caller: Principal,
    granted: frozenset[Permission],
) -> None:
    client, app = gateway
    client.portal.call(_bind, app, first, second)
    resolved = client.portal.call(partial(_resolve, app, caller))
    assert resolved.permissions == granted, "the resolved permissions"
    app.dependency_overrides[require_principal] = lambda: resolved
    wrong = [
        f"{probe.permission} — {probe.what}: {'allowed' if answer else 'refused'}"
        for probe in PROBES
        if (answer := probe.ask(client)) != (probe.permission in granted)
    ]
    assert not wrong, f"with {sorted(granted)}: {wrong}"


def test_every_permission_is_probed_on_one_plane_or_the_other() -> None:
    assert {probe.permission for probe in PROBES} | MANAGEMENT_ONLY == set(Permission)
    assert not {probe.permission for probe in PROBES} & MANAGEMENT_ONLY


@pytest.mark.parametrize(("first", "second"), COMBINATIONS, ids=[_id(c) for c in COMBINATIONS])
def test_two_custom_roles_give_exactly_their_union(
    gateway: tuple[TestClient, Any], first: Permission, second: Permission | None
) -> None:
    caller = Principal(subject="pair", method="oidc", groups=(GROUP_A, GROUP_B))
    granted = frozenset({first} | ({second} if second else set()))
    _assert_exactly(gateway, first, second, caller, granted)


@pytest.mark.parametrize(
    ("role", "extra"),
    [(role, extra) for role in Role for extra in ORDER],
    ids=[f"{role}+{extra}" for role in Role for extra in ORDER],
)
def test_a_builtin_role_and_a_custom_one_give_exactly_their_union(
    gateway: tuple[TestClient, Any], role: Role, extra: Permission
) -> None:
    caller = Principal(subject="pair", method="oidc", groups=(MAPPING[role][0], GROUP_A))
    granted = builtin_permissions([str(role)]) | {extra}
    _assert_exactly(gateway, extra, None, caller, granted)


@pytest.mark.parametrize("grant", ["admin", "user"])
@pytest.mark.parametrize("extra", ORDER, ids=str)
def test_a_grant_on_another_use_case_adds_no_permission(
    gateway: tuple[TestClient, Any], extra: Permission, grant: str
) -> None:
    """A grant is a relationship to its own use case: the probes about another one answer as if
    it were not there."""
    caller = Principal(
        subject="pair",
        method="oidc",
        groups=(GROUP_A,),
        use_cases=("own",),
        grants=(("own", grant),),
    )
    _assert_exactly(gateway, extra, None, caller, frozenset({extra}))
