"""The permission matrix on the live stack (`FRD-614` §6, `ADR-0025`).

The owner's acceptance condition, in the order it was asked for:

1. the Global Administrator is allowed every probe;
2. a role bound to a real Keycloak group starts empty and grows one permission at a time — after
   each step every probe answers exactly what has been granted, in Management at once and in the
   gateway once the change has travelled over `aira.roles`;
3. then changes — withdrawal, a second role (a union), rebinding, deletion — each checked the same
   way, and a withdrawal holds without a new token.

Every permission has a probe, and `test_every_permission_has_a_probe` fails on one nobody checks.
A probe answers *allowed* or *refused* and nothing else: any other status fails the test, so a
server error is never read as a refusal.

The realm is written to here — a group and a client for the test's own caller — and both are
removed at the end, as `test_access_by_group.py` does.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from aira_common.permissions import (
    ALL_PERMISSIONS,
    CATALOGUE,
    GRANTABLE,
    IT_SECURITY_PERMISSIONS,
    IT_STEUERUNG_DEFAULT,
    Permission,
)

from .conftest import (
    ADMIN_CLIENT_ID,
    ADMIN_CLIENT_SECRET,
    GATEWAY_URL,
    KEYCLOAK_URL,
    MANAGEMENT_URL,
    REALM,
    TEST_CLIENT_ID,
    TEST_CLIENT_SECRET,
    _token,
)

#: How long a change may take to reach the gateway: its read model is cached for five seconds, and
#: the event crosses the outbox relay and Kafka first.
PROPAGATION_SECONDS = 45.0
MODEL = "qwen3:0.6b"

P = Permission


@dataclass(frozen=True)
class Context:
    live: str
    retired: str
    rule_id: int
    trace_row: str


@dataclass(frozen=True)
class Probe:
    permission: Permission
    plane: str  # "management" or "gateway"
    what: str
    ask: Callable[[httpx.AsyncClient, str, Context], Awaitable[bool]]
    #: Which granted sets the probe answers *allowed* for — its own permission, unless another
    #: permission implies it (administering every use case includes seeing them).
    expected: Callable[[frozenset[Permission]], bool] | None = None

    def allows(self, granted: frozenset[Permission]) -> bool:
        return self.expected(granted) if self.expected else self.permission in granted


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _verdict(response: httpx.Response, allowed: set[int], refused: set[int], what: str) -> bool:
    if response.status_code in allowed:
        return True
    if response.status_code in refused:
        return False
    raise AssertionError(f"{what}: unexpected {response.status_code} {response.text[:300]}")


def _mgmt(method: str, path: str, allowed: set[int], refused: set[int], **body: Any):
    async def ask(client: httpx.AsyncClient, token: str, ctx: Context) -> bool:
        url = f"{MANAGEMENT_URL}{path.format(ctx=ctx)}"
        response = await client.request(method, url, headers=_auth(token), **body)
        return _verdict(response, allowed, refused, f"{method} {url}")

    return ask


def _gateway(path: str, allowed: set[int], refused: set[int]):
    async def ask(client: httpx.AsyncClient, token: str, ctx: Context) -> bool:
        url = f"{GATEWAY_URL}{path.format(ctx=ctx)}"
        response = await client.get(url, headers=_auth(token))
        return _verdict(response, allowed, refused, f"GET {url}")

    return ask


def _in_scope(path: str):
    """A list that answers an empty 200 outside the caller's scope and says so in `in_scope`."""

    async def ask(client: httpx.AsyncClient, token: str, ctx: Context) -> bool:
        url = f"{GATEWAY_URL}{path.format(ctx=ctx)}"
        response = await client.get(url, headers=_auth(token))
        assert response.status_code == 200, f"GET {url}: {response.status_code} {response.text}"
        return bool(response.json().get("in_scope"))

    return ask


def _sees_use_cases(granted: frozenset[Permission]) -> bool:
    return bool({P.USECASE_READ_ALL, P.USECASE_MANAGE_ALL} & granted)


PROBES: tuple[Probe, ...] = (
    Probe(
        P.USECASE_CREATE,
        "management",
        "create a use case",
        _mgmt("POST", "/api/v1/use-cases/", {400}, {403}, json={}),
    ),
    Probe(
        P.USECASE_READ_ALL,
        "management",
        "see a use case it holds no grant on",
        _mgmt("GET", "/api/v1/use-cases/{ctx.live}/", {200}, {404}),
        _sees_use_cases,
    ),
    Probe(
        P.USECASE_MANAGE_ALL,
        "management",
        "change a use case it holds no grant on",
        _mgmt(
            "PATCH",
            "/api/v1/use-cases/{ctx.live}/",
            {200},
            {403, 404},
            json={"name": "Permission matrix"},
        ),
    ),
    Probe(
        P.USECASE_READ_RETIRED,
        "management",
        "list retired use cases",
        _mgmt("GET", "/api/v1/use-cases/retired/", {200}, {403}),
    ),
    Probe(
        P.USECASE_PURGE,
        "management",
        "purge a retired use case (still waiting)",
        _mgmt("DELETE", "/api/v1/use-cases/{ctx.retired}/purge/", {400}, {403}),
    ),
    Probe(
        P.CATALOG_WRITE,
        "management",
        "declare a model",
        _mgmt("POST", "/api/v1/models/", {400}, {403}, json={}),
    ),
    Probe(
        P.CATALOG_WRITE,
        "gateway",
        "list what providers offer",
        _gateway("/v1beta/providers", {200}, {403}),
    ),
    Probe(
        P.BUDGET_INSTALLATION_WRITE,
        "management",
        "set the installation budget",
        _mgmt("POST", "/api/v1/installation-budgets/", {400}, {403}, json={}),
    ),
    Probe(
        P.REPORT_READ_ALL,
        "gateway",
        "read a use case's usage it is no member of",
        _gateway("/v1beta/usage/{ctx.live}", {200}, {403}),
    ),
    Probe(
        P.TRACE_READ_ALL,
        "gateway",
        "list a use case's requests",
        _in_scope("/v1beta/traces?use_case={ctx.live}"),
    ),
    Probe(
        P.ANOMALY_READ_ALL,
        "gateway",
        "list a use case's findings",
        _in_scope("/v1beta/anomalies?use_case={ctx.live}"),
    ),
    Probe(
        P.ANOMALY_RULE_GLOBAL_WRITE,
        "management",
        "change a global anomaly rule",
        _mgmt(
            "PATCH", "/api/v1/anomaly-rules/{ctx.rule_id}/", {200}, {403}, json={"enabled": False}
        ),
    ),
    Probe(
        P.INCIDENT_SUSPEND,
        "gateway",
        "list suspensions",
        _gateway("/v1beta/suspensions", {200}, {403}),
    ),
    Probe(
        P.INCIDENT_INVESTIGATE,
        "gateway",
        "filter requests by source address",
        _gateway("/v1beta/traces?source_ip=192.0.2.1", {200}, {403}),
    ),
    Probe(
        P.PAYLOAD_READ_ANY,
        "gateway",
        "open a request's stored content",
        _gateway("/v1beta/traces/{ctx.trace_row}/payload", {200}, {403, 404}),
    ),
    Probe(
        P.CONTENT_READ_READ,
        "gateway",
        "read the content-read log",
        _gateway("/v1beta/content-reads", {200}, {403}),
    ),
    Probe(
        P.OPERATIONS_DIAGNOSE,
        "gateway",
        "check a model",
        _gateway("/v1beta/models/matrix-probe-model:check", {200}, {403}),
    ),
    Probe(
        P.SMOKETEST_AUTHOR,
        "management",
        "write a question",
        _mgmt("POST", "/api/v1/test-cases/", {400}, {403}, json={}),
    ),
    Probe(
        P.SMOKETEST_RUN_ANY,
        "management",
        "open the question catalogue",
        _mgmt("GET", "/api/v1/test-cases/", {200}, {403}),
    ),
    Probe(
        P.DIRECTORY_SEARCH,
        "management",
        "search the directory",
        _mgmt("GET", "/api/v1/directory/?q=ma", {200}, {403}),
    ),
    Probe(
        P.ROLE_READ, "management", "list the roles", _mgmt("GET", "/api/v1/roles/", {200}, {403})
    ),
    Probe(
        P.ROLE_MANAGE,
        "management",
        "check a group for binding",
        _mgmt("POST", "/api/v1/roles/check-group/", {400}, {403}, json={"group_path": "/"}),
    ),
)


def test_every_permission_has_a_probe() -> None:
    """A permission nobody probes is a checkbox nobody has shown to do anything."""
    assert {probe.permission for probe in PROBES} == set(Permission)


# == the matrix ===================================================================================


async def _answers(
    client: httpx.AsyncClient, token: str, ctx: Context, plane: str
) -> dict[str, tuple[bool, bool]]:
    """Each probe's answer on one plane, beside what it should be — filled in by the caller."""
    found: dict[str, tuple[bool, bool]] = {}
    for probe in PROBES:
        if probe.plane == plane:
            found[f"{probe.permission} — {probe.what}"] = (
                await probe.ask(client, token, ctx),
                False,
            )
    return found


async def assert_matrix(
    client: httpx.AsyncClient,
    token: str,
    ctx: Context,
    granted: frozenset[Permission],
    step: str,
    also: frozenset[Permission] = frozenset(),
) -> None:
    """Every probe answers exactly ``granted``: Management at once, the gateway within
    :data:`PROPAGATION_SECONDS`. ``also`` is a door a grant on a use case opens without being a
    permission — it never appears in `/me`."""

    def wrong(plane: str, answers: dict[str, tuple[bool, bool]]) -> list[str]:
        out = []
        for probe in PROBES:
            if probe.plane != plane:
                continue
            key = f"{probe.permission} — {probe.what}"
            if answers[key][0] != probe.allows(granted | also):
                out.append(f"{key}: {'allowed' if answers[key][0] else 'refused'}")
        return out

    management = wrong("management", await _answers(client, token, ctx, "management"))
    assert not management, f"{step} — Management disagrees with {sorted(granted)}: {management}"

    me = (await client.get(f"{MANAGEMENT_URL}/api/v1/me", headers=_auth(token))).json()
    assert set(me["permissions"]) == {str(p) for p in granted}, f"{step} — /me"

    deadline = time.monotonic() + PROPAGATION_SECONDS
    while True:
        gateway = wrong("gateway", await _answers(client, token, ctx, "gateway"))
        if not gateway:
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"{step} — the gateway still disagrees: {gateway}")
        await asyncio.sleep(1.0)


# == the realm, for the test's own caller =========================================================


async def _kc_admin(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": "admin",
            "grant_type": "password",
        },
    )
    assert response.status_code == 200, response.text
    return _auth(response.json()["access_token"])


def _created_id(response: httpx.Response) -> str:
    assert response.status_code == 201, response.text
    return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


class Realm:
    """A group tree and a confidential client whose service account joins its groups."""

    def __init__(self, client: httpx.AsyncClient, suffix: str) -> None:
        self.client = client
        self.base = f"{KEYCLOAK_URL}/admin/realms/{REALM}"
        self.top = f"aira-matrix-{suffix}"
        self.client_id = f"aira-matrix-{suffix}"
        self.secret = secrets.token_urlsafe(24)
        self.groups: dict[str, str] = {}
        self._client_uuid = ""
        self._user_id = ""

    def path(self, name: str) -> str:
        return f"/{self.top}/{name}"

    async def create(self, *children: str) -> None:
        admin = await _kc_admin(self.client)
        top = _created_id(
            await self.client.post(f"{self.base}/groups", headers=admin, json={"name": self.top})
        )
        self.groups["__top__"] = top
        for name in children:
            self.groups[name] = _created_id(
                await self.client.post(
                    f"{self.base}/groups/{top}/children", headers=admin, json={"name": name}
                )
            )
        # The mappers every AIRA client carries: `groups` with full paths, and the gateway as
        # audience. Copied from an existing client so this test cannot drift from the realm.
        template = (
            await self.client.get(
                f"{self.base}/clients", headers=admin, params={"clientId": ADMIN_CLIENT_ID}
            )
        ).json()[0]
        mappers = [
            {k: v for k, v in mapper.items() if k != "id"}
            for mapper in template.get("protocolMappers", [])
        ]
        self._client_uuid = _created_id(
            await self.client.post(
                f"{self.base}/clients",
                headers=admin,
                json={
                    "clientId": self.client_id,
                    "secret": self.secret,
                    "enabled": True,
                    "publicClient": False,
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "protocol": "openid-connect",
                    "protocolMappers": mappers,
                },
            )
        )
        account = await self.client.get(
            f"{self.base}/clients/{self._client_uuid}/service-account-user", headers=admin
        )
        self._user_id = account.json()["id"]

    async def join(self, name: str, *, member: bool = True) -> None:
        admin = await _kc_admin(self.client)
        url = f"{self.base}/users/{self._user_id}/groups/{self.groups[name]}"
        response = await (
            self.client.put(url, headers=admin)
            if member
            else self.client.delete(url, headers=admin)
        )
        assert response.status_code in (200, 204), response.text

    async def join_path(self, path: str, *, member: bool = True) -> None:
        """Join, or leave, a group the realm already has — a built-in role's group."""
        admin = await _kc_admin(self.client)
        found = await self.client.get(f"{self.base}/group-by-path{path}", headers=admin)
        assert found.status_code == 200, found.text
        url = f"{self.base}/users/{self._user_id}/groups/{found.json()['id']}"
        response = await (
            self.client.put(url, headers=admin)
            if member
            else self.client.delete(url, headers=admin)
        )
        assert response.status_code in (200, 204), response.text

    async def token(self) -> str:
        return await _token(self.client_id, self.secret)

    async def remove(self) -> None:
        admin = await _kc_admin(self.client)
        if self._client_uuid:
            await self.client.delete(f"{self.base}/clients/{self._client_uuid}", headers=admin)
        if "__top__" in self.groups:
            await self.client.delete(f"{self.base}/groups/{self.groups['__top__']}", headers=admin)


# == what the probes need =========================================================================


async def _context(client: httpx.AsyncClient, admin: str, suffix: str) -> Context:
    """A live use case the caller holds no grant on, a retired one, a global rule, and one
    recorded request — each made by the Global Administrator."""
    headers = _auth(admin)
    live, retired = f"matrix-{suffix}", f"matrix-gone-{suffix}"
    for slug in (live, retired):
        created = await client.post(
            f"{MANAGEMENT_URL}/api/v1/use-cases/",
            headers=headers,
            json={"slug": slug, "name": "Permission matrix"},
        )
        assert created.status_code == 201, created.text
    assert (
        await client.delete(f"{MANAGEMENT_URL}/api/v1/use-cases/{retired}/", headers=headers)
    ).status_code == 204

    rule = await client.post(
        f"{MANAGEMENT_URL}/api/v1/anomaly-rules/",
        headers=headers,
        json={
            "name": f"matrix probe {suffix}",
            "kind": "refusal_rate",
            "window_minutes": 15,
            "threshold": 99,
            "min_sample": 1000,
            "enabled": False,
        },
    )
    assert rule.status_code == 201, rule.text

    # One recorded request in the live use case: a key, then a call. Nothing is released to the
    # use case, so the gateway refuses the model — and records the refusal, which is a row.
    issued = await client.post(
        f"{MANAGEMENT_URL}/api/v1/use-cases/{live}/api-keys/",
        headers=headers,
        json={"label": "permission-matrix"},
    )
    assert issued.status_code == 201, issued.text
    body = issued.json()
    key = body.get("key") or body.get("secret") or body.get("api_key")
    assert key, f"no secret in {sorted(body)}"
    deadline = time.monotonic() + PROPAGATION_SECONDS
    while True:
        called = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers={"x-goog-api-key": key},
            json={"contents": [{"role": "user", "parts": [{"text": "matrix"}]}]},
        )
        if called.status_code != 401:
            break
        assert time.monotonic() < deadline, "the key never reached the gateway"
        await asyncio.sleep(1.0)
    while True:
        rows = (
            await client.get(
                f"{GATEWAY_URL}/v1beta/traces", headers=headers, params={"use_case": live}
            )
        ).json()["traces"]
        if rows:
            break
        assert time.monotonic() < deadline, "the refused call was never recorded"
        await asyncio.sleep(1.0)
    return Context(
        live=live, retired=retired, rule_id=int(rule.json()["id"]), trace_row=rows[0]["id"]
    )


async def _set(client: httpx.AsyncClient, admin: str, slug: str, **body: Any) -> dict[str, Any]:
    response = await client.patch(
        f"{MANAGEMENT_URL}/api/v1/roles/{slug}/", headers=_auth(admin), json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ordered(granted: set[Permission]) -> list[str]:
    return [str(p) for p in CATALOGUE if p in granted]


# == the owner's sequence =========================================================================


async def test_the_matrix_grows_changes_and_holds() -> None:
    suffix = secrets.token_hex(3)
    async with httpx.AsyncClient(timeout=60.0) as client:
        realm = Realm(client, suffix)
        admin = await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET)
        roles: list[str] = []
        try:
            await realm.create("one", "two", "three")
            ctx = await _context(client, admin, suffix)

            # 1. The Global Administrator holds everything.
            await assert_matrix(client, admin, ctx, ALL_PERMISSIONS, "global administrator")

            # 2. A role on a real group, empty.
            created = await client.post(
                f"{MANAGEMENT_URL}/api/v1/roles/",
                headers=_auth(admin),
                json={
                    "label": f"Matrix {suffix}",
                    "group_path": realm.path("one"),
                    "permissions": [],
                },
            )
            assert created.status_code == 201, created.text
            role = created.json()["slug"]
            roles.append(role)
            await realm.join("one")
            token = await realm.token()
            granted: set[Permission] = set()
            await assert_matrix(client, token, ctx, frozenset(granted), "bound, empty")

            # 3. Growing, one permission at a time, the whole matrix after each.
            for permission in (p for p in CATALOGUE if p in GRANTABLE):
                granted.add(permission)
                await _set(client, admin, role, permissions=_ordered(granted))
                await assert_matrix(client, token, ctx, frozenset(granted), f"+ {permission}")

            # 4. Withdrawing every other permission, then granting them back — same token.
            withdrawn = {p for i, p in enumerate(sorted(granted, key=str)) if i % 2 == 0}
            await _set(client, admin, role, permissions=_ordered(granted - withdrawn))
            await assert_matrix(
                client, token, ctx, frozenset(granted - withdrawn), "withdrawn half"
            )
            await _set(client, admin, role, permissions=_ordered(granted))
            await assert_matrix(client, token, ctx, frozenset(granted), "granted back")
            await _set(client, admin, role, permissions=_ordered(withdrawn))
            await assert_matrix(client, token, ctx, frozenset(withdrawn), "the other half")

            # 5. Narrow the first role, add a second on another group: a union.
            first = {P.REPORT_READ_ALL, P.TRACE_READ_ALL}
            await _set(client, admin, role, permissions=_ordered(first))
            second = await client.post(
                f"{MANAGEMENT_URL}/api/v1/roles/",
                headers=_auth(admin),
                json={
                    "label": f"Matrix second {suffix}",
                    "group_path": realm.path("two"),
                    "permissions": _ordered({P.INCIDENT_SUSPEND}),
                },
            )
            assert second.status_code == 201, second.text
            roles.append(second.json()["slug"])
            await assert_matrix(client, token, ctx, frozenset(first), "second role, not a member")
            await realm.join("two")
            token = await realm.token()  # a new membership arrives in a new token
            await assert_matrix(
                client, token, ctx, frozenset(first | {P.INCIDENT_SUSPEND}), "union of two roles"
            )

            # 6. Rebinding the first role to a group the caller is not in takes it away.
            await _set(client, admin, role, group_path=realm.path("three"))
            await assert_matrix(client, token, ctx, frozenset({P.INCIDENT_SUSPEND}), "rebound")

            # 7. Deleting the second role leaves nothing.
            deleted = await client.delete(
                f"{MANAGEMENT_URL}/api/v1/roles/{roles.pop()}/", headers=_auth(admin)
            )
            assert deleted.status_code == 204, deleted.text
            await assert_matrix(client, token, ctx, frozenset(), "deleted")

            # 8. The boundaries.
            for fixed in ("global-admin", "it-security"):
                refused = await client.patch(
                    f"{MANAGEMENT_URL}/api/v1/roles/{fixed}/",
                    headers=_auth(admin),
                    json={"permissions": []},
                )
                assert refused.status_code == 403, refused.text
            unknown = await client.post(
                f"{MANAGEMENT_URL}/api/v1/roles/",
                headers=_auth(admin),
                json={"label": f"Nowhere {suffix}", "group_path": realm.path("nowhere")},
            )
            assert unknown.status_code == 400, unknown.text
            reserved = await client.post(
                f"{MANAGEMENT_URL}/api/v1/roles/",
                headers=_auth(admin),
                json={
                    "label": f"Raise {suffix}",
                    "group_path": realm.path("two"),
                    "permissions": ["role.manage"],
                },
            )
            assert reserved.status_code == 400, reserved.text
        finally:
            for slug in roles:
                await client.delete(f"{MANAGEMENT_URL}/api/v1/roles/{slug}/", headers=_auth(admin))
            await realm.remove()


async def test_it_steuerung_can_be_narrowed_and_is_restored() -> None:
    """A built-in role that may be changed, changed on the live stack and put back."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin = await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET)
        governance = await _token(TEST_CLIENT_ID, TEST_CLIENT_SECRET)
        suffix = secrets.token_hex(3)
        ctx = await _context(client, admin, suffix)
        try:
            await assert_matrix(client, governance, ctx, IT_STEUERUNG_DEFAULT, "IT Steuerung")
            await _set(client, admin, "it-steuerung", permissions=["report.read_all"])
            await assert_matrix(
                client, governance, ctx, frozenset({P.REPORT_READ_ALL}), "IT Steuerung narrowed"
            )
        finally:
            await _set(
                client, admin, "it-steuerung", permissions=_ordered(set(IT_STEUERUNG_DEFAULT))
            )
        await assert_matrix(client, governance, ctx, IT_STEUERUNG_DEFAULT, "IT Steuerung restored")


async def _doors(client: httpx.AsyncClient, token: str, ctx: Context) -> frozenset[Permission]:
    """Which probes open for this caller, whatever opened them."""
    return frozenset([probe.permission for probe in PROBES if await probe.ask(client, token, ctx)])


async def _role(
    client: httpx.AsyncClient, admin: str, label: str, group: str, held: set[Permission]
) -> str:
    created = await client.post(
        f"{MANAGEMENT_URL}/api/v1/roles/",
        headers=_auth(admin),
        json={"label": label, "group_path": group, "permissions": _ordered(held)},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["slug"])


async def test_two_groups_give_exactly_the_union_on_the_live_stack() -> None:
    """The pairwise matrix's live samples (`FRD-614` FR-7): a built-in role beside a custom one,
    two roles holding the same permission, an empty role beside a full one, and a role beside a
    grant on another use case. Every pair of single permissions is the hermetic matrix's
    (`test_the_pairwise_permission_matrix.py` in both planes); these are the combinations that also
    need a real token, a real group and the change travelling to the gateway."""
    suffix = secrets.token_hex(3)
    async with httpx.AsyncClient(timeout=60.0) as client:
        realm = Realm(client, suffix)
        admin = await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET)
        roles: list[str] = []
        own = f"matrix-own-{suffix}"
        try:
            await realm.create("one", "two")
            ctx = await _context(client, admin, suffix)
            first = await _role(client, admin, f"Union A {suffix}", realm.path("one"), set())
            roles.append(first)
            await realm.join("one")

            # A built-in role beside a custom one. A built-in group may hold grants of its own in
            # an installation, and an admin grant opens documented doors; so what the group opens
            # alone is measured first, and the union may add the custom permission to exactly that.
            for group, held, extra in (
                ("/aira/it-security", IT_SECURITY_PERMISSIONS, P.USECASE_CREATE),
                ("/aira/it-steuerung", IT_STEUERUNG_DEFAULT, P.BUDGET_INSTALLATION_WRITE),
            ):
                await _set(client, admin, first, permissions=[])
                await realm.join_path(group)
                token = await realm.token()
                alone = await _doors(client, token, ctx)
                await _set(client, admin, first, permissions=_ordered({extra}))
                await assert_matrix(
                    client,
                    token,
                    ctx,
                    held | {extra},
                    f"{group} beside a custom role",
                    also=alone - held,
                )
                await realm.join_path(group, member=False)

            # Two roles holding the same permission.
            await _set(
                client, admin, first, permissions=_ordered({P.REPORT_READ_ALL, P.TRACE_READ_ALL})
            )
            second = await _role(
                client, admin, f"Union B {suffix}", realm.path("two"), {P.REPORT_READ_ALL}
            )
            roles.append(second)
            await realm.join("two")
            token = await realm.token()
            await assert_matrix(
                client,
                token,
                ctx,
                frozenset({P.REPORT_READ_ALL, P.TRACE_READ_ALL}),
                "two roles holding the same permission",
            )

            # An empty role beside a full one.
            await _set(client, admin, first, permissions=[])
            await _set(
                client,
                admin,
                second,
                permissions=_ordered({P.REPORT_READ_ALL, P.USECASE_READ_RETIRED}),
            )
            both = frozenset({P.REPORT_READ_ALL, P.USECASE_READ_RETIRED})
            await assert_matrix(client, token, ctx, both, "an empty role beside a full one")

            # A role beside an admin grant on another use case: the grant opens the doors it
            # documents for somebody who administers a use case, and no permission.
            created = await client.post(
                f"{MANAGEMENT_URL}/api/v1/use-cases/",
                headers=_auth(admin),
                json={"slug": own, "name": "Permission matrix, own"},
            )
            assert created.status_code == 201, created.text
            granted = await client.post(
                f"{MANAGEMENT_URL}/api/v1/use-cases/{own}/groups/",
                headers=_auth(admin),
                json={"group_path": realm.path("one"), "role": "admin"},
            )
            assert granted.status_code in (200, 201), granted.text
            await assert_matrix(
                client,
                token,
                ctx,
                both,
                "a role beside an admin grant on another use case",
                also=frozenset({P.SMOKETEST_RUN_ANY, P.DIRECTORY_SEARCH}),
            )
        finally:
            for slug in roles:
                await client.delete(f"{MANAGEMENT_URL}/api/v1/roles/{slug}/", headers=_auth(admin))
            await client.delete(f"{MANAGEMENT_URL}/api/v1/use-cases/{own}/", headers=_auth(admin))
            await realm.remove()


pytestmark = pytest.mark.integration
