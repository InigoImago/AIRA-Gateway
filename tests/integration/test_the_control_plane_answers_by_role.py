"""Every Management surface, every role, and none — over the running service (`FRD-201`).

The RBAC unit tests are thorough about each predicate. What none of them asks is the question a
pen test asks: **hold up a real token and see what the running service does.** The predicates are
right and a viewset that forgets to call one is still right in every test of the predicate.

Three things this file gets from being live that a unit test cannot. The token is a real Keycloak
token, so the roles come from the `groups` claim through the configured mapping rather than from a
fixture that sets them directly (`ADR-0017`). The tenancy positions are real object grants reached
through a real Keycloak group, which is the path `FRD-209` exists for. And an unauthenticated call
meets the actual authentication class, so `401` is the service's answer rather than a test client's.

**Six positions, and the ones that matter are the last three.** An RBAC test with only privileged
callers proves nothing about who is excluded, which is the half a pen test is for.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import (
    ADMIN_CLIENT_ID,
    ADMIN_CLIENT_SECRET,
    MANAGEMENT_URL,
    MEMBER_CLIENT_ID,
    MEMBER_CLIENT_SECRET,
    SECURITY_CLIENT_ID,
    SECURITY_CLIENT_SECRET,
    TEST_CLIENT_ID,
    TEST_CLIENT_SECRET,
    _token,
)

pytestmark = pytest.mark.integration

#: The Keycloak group the member account is in. A grant to it is how that account reaches a use
#: case at all — there is no direct membership anywhere in this file, on purpose.
MEMBER_GROUP = "/abteilungen/kundendienst"


@pytest.fixture(scope="module")
async def tokens() -> dict[str, str]:
    return {
        "global-admin": await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET),
        "it-security": await _token(SECURITY_CLIENT_ID, SECURITY_CLIENT_SECRET),
        "it-steuerung": await _token(TEST_CLIENT_ID, TEST_CLIENT_SECRET),
        "member": await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET),
    }


def _headers(tokens: dict[str, str], who: str) -> dict[str, str]:
    if who == "anonymous":
        return {}
    if who == "nonsense":
        return {"Authorization": "Bearer not.a.token"}
    return {"Authorization": f"Bearer {tokens[who]}"}


async def _use_case(client: httpx.AsyncClient, admin: dict[str, str], grant: str | None) -> str:
    """A use case created by a Global Administrator, optionally granting the member's group.

    A **fresh one per cell**. The first version of this sweep shared one use case across the whole
    matrix and its own earlier rows granted access to the account the later rows were meant to
    exclude — so the outsider column reported a member's answers. A matrix whose cells are not
    independent measures the order it was written in.
    """
    slug = f"itest-rbac-{uuid.uuid4().hex[:8]}"
    made = await client.post("/api/v1/use-cases/", json={"name": slug, "slug": slug}, headers=admin)
    assert made.status_code == 201, made.text
    if grant is not None:
        granted = await client.post(
            f"/api/v1/use-cases/{slug}/groups/",
            json={"group_path": MEMBER_GROUP, "role": grant},
            headers=admin,
        )
        assert granted.status_code in (200, 201), granted.text
    return slug


#: `(name, method, path suffix, body)` — everything a use case's own surface offers.
CALLS: list[tuple[str, str, str, dict | None]] = [
    ("read", "get", "", None),
    ("edit", "patch", "", {"description": "x"}),
    ("retire", "delete", "", None),
    ("read members", "get", "members/", None),
    ("add a member", "post", "members/", {"username": "ucuser", "role": "user"}),
    ("read grants", "get", "groups/", None),
    # A group **nobody in this file holds**. The first version granted `/aira/it-security` admin
    # rights and then asserted, two cases later, that IT Security is excluded from the directory —
    # a matrix that creates the condition it goes on to deny. `IsGlobalAdminOrUseCaseAdministrator`
    # asks whether somebody administers *any* use case, so a grant made here is installation-wide
    # state that outlives the cell that made it.
    ("grant a group", "post", "groups/", {"group_path": "/abteilungen/keine", "role": "admin"}),
    ("read keys", "get", "api-keys/", None),
    ("issue a key", "post", "api-keys/", {"label": "itest"}),
    ("read the pipeline", "get", "pipeline/", None),
    ("replace the pipeline", "put", "pipeline/", {"steps": [], "fallback_models": []}),
    ("read budgets", "get", "budgets/", None),
    ("read limits", "get", "rate-limits/", None),
    ("set a limit", "post", "rate-limits/", {"scope": "use_case", "limit_rpm": 10}),
    ("read anomaly rules", "get", "anomaly-rules/", None),
    ("purge", "delete", "purge/", None),
]

#: `position -> (which token, what the member's group was granted)`.
POSITIONS: dict[str, tuple[str, str | None]] = {
    "global-admin": ("global-admin", None),
    "it-security": ("it-security", None),
    "it-steuerung": ("it-steuerung", None),
    "member": ("member", "user"),
    "use-case-admin": ("member", "admin"),
    "outsider": ("member", None),
    "anonymous": ("anonymous", None),
    "nonsense": ("nonsense", None),
}

#: What each position must get, per call. Written out rather than derived, because a table derived
#: from the same predicates the service uses would agree with a wrong predicate.
#:
#: The two rules the shape of this table encodes:
#:
#: - **The oversight roles read and never write** (`ADR-0007`, PRD §154). They see every use case
#:   and may change none of it — including issuing an API key, which is data-plane access and so
#:   deliberately withheld from the roles that can see everything.
#: - **A selector never grants access.** Somebody with no grant on a use case is answered `404`
#:   for its every route, not `403`: telling the two apart would confirm the slug exists.
EXPECTED: dict[str, dict[str, int]] = {
    "global-admin": {
        "read": 200,
        "edit": 200,
        "retire": 204,
        "read members": 200,
        "add a member": 201,
        "read grants": 200,
        "grant a group": 201,
        "read keys": 200,
        "issue a key": 201,
        "read the pipeline": 200,
        "replace the pipeline": 200,
        "read budgets": 200,
        "read limits": 200,
        "set a limit": 201,
        "read anomaly rules": 200,
        "purge": 404,
    },
    "it-security": {
        "read": 200,
        "edit": 403,
        "retire": 403,
        "read members": 200,
        "add a member": 403,
        "read grants": 200,
        "grant a group": 403,
        "read keys": 200,
        "issue a key": 403,
        "read the pipeline": 200,
        "replace the pipeline": 403,
        "read budgets": 200,
        "read limits": 200,
        "set a limit": 403,
        "read anomaly rules": 200,
        "purge": 403,
    },
    "it-steuerung": {
        "read": 200,
        "edit": 403,
        "retire": 403,
        "read members": 200,
        "add a member": 403,
        "read grants": 200,
        "grant a group": 403,
        "read keys": 200,
        "issue a key": 403,
        "read the pipeline": 200,
        "replace the pipeline": 403,
        "read budgets": 200,
        "read limits": 200,
        "set a limit": 403,
        "read anomaly rules": 200,
        "purge": 403,
    },
    # A member may read their use case and issue themselves a key — and change nothing else.
    # The key is `FRD-205`'s deliberate exception: it is bound to this use case and owned by them.
    "member": {
        "read": 200,
        "edit": 403,
        "retire": 403,
        "read members": 200,
        "add a member": 403,
        "read grants": 200,
        "grant a group": 403,
        "read keys": 200,
        "issue a key": 201,
        "read the pipeline": 200,
        "replace the pipeline": 403,
        "read budgets": 200,
        "read limits": 200,
        "set a limit": 403,
        "read anomaly rules": 200,
        "purge": 403,
    },
    "use-case-admin": {
        "read": 200,
        "edit": 200,
        "retire": 204,
        "read members": 200,
        "add a member": 201,
        "read grants": 200,
        "grant a group": 201,
        "read keys": 200,
        "issue a key": 201,
        "read the pipeline": 200,
        "replace the pipeline": 200,
        "read budgets": 200,
        "read limits": 200,
        "set a limit": 201,
        "read anomaly rules": 200,
        # Not the person who retired it — `FRD-607` splits the two decisions.
        "purge": 403,
    },
    "outsider": dict.fromkeys([name for name, _, _, _ in CALLS], 404) | {"purge": 403},
    "anonymous": dict.fromkeys([name for name, _, _, _ in CALLS], 401),
    "nonsense": dict.fromkeys([name for name, _, _, _ in CALLS], 401),
}


@pytest.mark.parametrize("position", list(POSITIONS), ids=list(POSITIONS))
@pytest.mark.parametrize("call", CALLS, ids=[name for name, _, _, _ in CALLS])
async def test_a_use_case_surface_answers_by_position(
    tokens: dict[str, str], position: str, call: tuple[str, str, str, dict | None]
) -> None:
    """128 cells: sixteen routes over eight positions, each on a use case of its own."""
    name, verb, suffix, body = call
    who, grant = POSITIONS[position]
    admin = _headers(tokens, "global-admin")

    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=30.0) as client:
        slug = await _use_case(client, admin, grant)
        kwargs: dict = {"headers": _headers(tokens, who)}
        if body is not None:
            kwargs["json"] = body
        response = await getattr(client, verb)(f"/api/v1/use-cases/{slug}/{suffix}", **kwargs)

    assert response.status_code == EXPECTED[position][name], (
        f"{position} · {name}: {response.status_code}, expected "
        f"{EXPECTED[position][name]} — {response.text[:200]}"
    )


#: The surfaces that belong to the installation rather than to one use case.
INSTALLATION: list[tuple[str, str, str, dict | None, dict[str, int]]] = [
    (
        "create a use case",
        "post",
        "/api/v1/use-cases/",
        {"name": "itest-rbac-new", "slug": "itest-rbac-new-x"},
        {
            "global-admin": 201,
            "it-security": 403,
            "it-steuerung": 403,
            "member": 403,
            "anonymous": 401,
            "nonsense": 401,
        },
    ),
    (
        "the retired list",
        "get",
        "/api/v1/use-cases/retired/",
        None,
        # Governance only, and `FRD-607` FR-4 says so in as many words. IT Security is an
        # *oversight* role and is deliberately not one of them here — asserted so that widening it
        # has to be a decision somebody takes rather than a line somebody changes.
        {
            "global-admin": 200,
            "it-security": 403,
            "it-steuerung": 200,
            "member": 403,
            "anonymous": 401,
            "nonsense": 401,
        },
    ),
    (
        "catalogue a model",
        "post",
        "/api/v1/models/",
        {"name": "itest-rbac-model", "provider": "mock"},
        {
            "global-admin": 201,
            "it-security": 403,
            "it-steuerung": 403,
            "member": 403,
            "anonymous": 401,
            "nonsense": 401,
        },
    ),
    (
        "read the catalogue",
        "get",
        "/api/v1/models/",
        None,
        {
            "global-admin": 200,
            "it-security": 200,
            "it-steuerung": 200,
            "member": 200,
            "anonymous": 401,
            "nonsense": 401,
        },
    ),
    (
        "an installation budget",
        "post",
        "/api/v1/installation-budgets/",
        {"period": "daily", "limit_requests": 100},
        # 400 for the one who may: the body is deliberately incomplete, and what is under test is
        # that everybody else is stopped **before** the body is looked at.
        {
            "global-admin": 400,
            "it-security": 403,
            "it-steuerung": 403,
            "member": 403,
            "anonymous": 401,
            "nonsense": 401,
        },
    ),
    (
        "the directory",
        "get",
        "/api/v1/directory/?q=uc",
        None,
        # **Not a role gate**, and that is worth stating rather than asserting around.
        # `IsGlobalAdminOrUseCaseAdministrator` asks whether this person administers *any* use
        # case — a fact about the whole installation's grants, not about the caller's roles and
        # not about this request. So the only answers stable on a live stack are the two ends: a
        # Global Administrator always passes, and a caller with no valid token never does.
        # Asserting `403` for the roles in between would be asserting that no test, seed or
        # operator has ever granted their group administration of anything, which is not a
        # property of this endpoint — and the first version of this file disproved it itself,
        # two cases earlier, by granting exactly that.
        {"global-admin": 200, "anonymous": 401, "nonsense": 401},
    ),
]


@pytest.mark.parametrize(
    ("name", "verb", "path", "body", "expected"),
    INSTALLATION,
    ids=[row[0].replace(" ", "-") for row in INSTALLATION],
)
@pytest.mark.parametrize(
    "position", ["global-admin", "it-security", "it-steuerung", "member", "anonymous", "nonsense"]
)
async def test_an_installation_surface_answers_by_role(
    tokens: dict[str, str],
    position: str,
    name: str,
    verb: str,
    path: str,
    body: dict | None,
    expected: dict[str, int],
) -> None:
    """36 more cells, on the surfaces no use case owns.

    The member here holds **no** organisation-wide role and no grant anywhere, which is what makes
    the `403`s worth asserting: they are the answer for an ordinary authenticated person.
    """
    del name
    if position not in expected:
        pytest.skip("this position's answer is installation state rather than a role — see above")
    if body is not None and "slug" in body:
        body = {**body, "slug": f"{body['slug']}-{uuid.uuid4().hex[:6]}"}
    if body is not None and path.endswith("models/"):
        body = {**body, "name": f"{body['name']}-{uuid.uuid4().hex[:6]}"}

    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=30.0) as client:
        kwargs: dict = {"headers": _headers(tokens, POSITIONS[position][0])}
        if body is not None:
            kwargs["json"] = body
        response = await getattr(client, verb)(path, **kwargs)

    assert response.status_code == expected[position], (
        f"{position}: {response.status_code}, expected {expected[position]} — {response.text[:200]}"
    )
