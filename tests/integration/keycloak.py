"""The realm's administration API, for the few tests that must write to it.

AIRA never writes to a directory; these tests do, to put an account into a group the way an
administrator would. So every group and client made here is registered (`installation.py`) the
moment it exists, and the session removes it whatever the test's outcome.

**The master administrator is the stack's, not a literal.** Compose takes it from
`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`; a stack started with another password made three
copies of `"admin"` die on a bare `KeyError`.

**A token per call.** The master realm's tokens live sixty seconds. A test that fetched one at its
start and used it again in a `finally` after a slow model call was refused there — and the group it
meant to remove stayed in the realm.
"""

from __future__ import annotations

import httpx
import stack_addresses

from . import installation

KEYCLOAK_URL = stack_addresses.url("keycloak")
REALM = "aira"


def base() -> str:
    return f"{KEYCLOAK_URL}/admin/realms/{REALM}"


def _login() -> dict[str, str]:
    return {
        "client_id": "admin-cli",
        "grant_type": "password",
        "username": stack_addresses.setting("KEYCLOAK_ADMIN"),
        "password": stack_addresses.setting("KEYCLOAK_ADMIN_PASSWORD"),
    }


def _headers(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 200, f"no master-realm token: {response.text}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def admin_headers_sync(client: httpx.Client) -> dict[str, str]:
    return _headers(
        client.post(f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token", data=_login())
    )


async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return _headers(
        await client.post(
            f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token", data=_login()
        )
    )


def created_id(response: httpx.Response) -> str:
    """The id Keycloak gives what it just made, from `Location` — refusing anything but a 201."""
    assert response.status_code == 201, response.text
    return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


# == groups ========================================================================================


def _group_path_url(path: str) -> str:
    return f"{base()}/group-by-path{path}"


async def group_id(client: httpx.AsyncClient, path: str) -> str:
    """The id of a group the realm already has, found by its **exact** path."""
    found = await client.get(_group_path_url(path), headers=await admin_headers(client))
    assert found.status_code == 200, f"no group {path}: {found.text}"
    return str(found.json()["id"])


async def create_group(client: httpx.AsyncClient, name: str, *, parent: str | None = None) -> str:
    """Make a group (under ``parent``, a path, when given), registered for removal."""
    url = f"{base()}/groups"
    if parent is not None:
        url = f"{base()}/groups/{await group_id(client, parent)}/children"
    made = created_id(
        await client.post(url, headers=await admin_headers(client), json={"name": name})
    )
    installation.remember("group", made)
    return made


async def user_id(client: httpx.AsyncClient, username: str) -> str:
    found = await client.get(
        f"{base()}/users",
        headers=await admin_headers(client),
        params={"username": username, "exact": "true"},
    )
    assert found.status_code == 200 and found.json(), f"no user {username}: {found.text}"
    return str(found.json()[0]["id"])


async def set_membership(client: httpx.AsyncClient, user: str, group: str, *, member: bool) -> None:
    """Put the user with id ``user`` into the group with id ``group``, or take them out."""
    url = f"{base()}/users/{user}/groups/{group}"
    headers = await admin_headers(client)
    response = await (
        client.put(url, headers=headers) if member else client.delete(url, headers=headers)
    )
    assert response.status_code in (200, 204), response.text


async def delete_group(client: httpx.AsyncClient, group: str) -> None:
    """Remove a group; one already gone is what a removal wanted."""
    response = await client.delete(f"{base()}/groups/{group}", headers=await admin_headers(client))
    assert response.status_code in (204, 404), response.text


# == the same, for a session-scoped fixture, which has no event loop of its own ====================


def department_for(username: str, name: str, *, parent: str = "/abteilungen") -> tuple[str, str]:
    """A group under ``parent`` that holds ``username`` and nobody else: ``(path, id)``."""
    with httpx.Client(timeout=30.0) as client:
        parent_found = client.get(_group_path_url(parent), headers=admin_headers_sync(client))
        assert parent_found.status_code == 200, parent_found.text
        made = created_id(
            client.post(
                f"{base()}/groups/{parent_found.json()['id']}/children",
                headers=admin_headers_sync(client),
                json={"name": name},
            )
        )
        installation.remember("group", made)
        users = client.get(
            f"{base()}/users",
            headers=admin_headers_sync(client),
            params={"username": username, "exact": "true"},
        )
        assert users.status_code == 200 and users.json(), users.text
        joined = client.put(
            f"{base()}/users/{users.json()[0]['id']}/groups/{made}",
            headers=admin_headers_sync(client),
        )
        assert joined.status_code in (200, 204), joined.text
    return f"{parent}/{name}", made


def remove_group_sync(group: str) -> None:
    with httpx.Client(timeout=30.0) as client:
        response = client.delete(f"{base()}/groups/{group}", headers=admin_headers_sync(client))
    assert response.status_code in (204, 404), response.text
