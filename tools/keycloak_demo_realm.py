"""Keep the *development* realm matching the file that defines it.

Keycloak imports a realm **only if it does not exist** — `Realm 'aira' already exists. Import
skipped`. So every change to `deploy/compose/keycloak/realms/aira-realm.json` reaches a fresh
machine and no other: new groups, a changed client, a mapper, a demo password. A colleague who had
run this stack before `ADR-0017` moved roles onto groups keeps a realm from before it, and the
symptom is whatever happens to be missing first. The one reported was the plainest possible:
`make showcase` prints five accounts and Keycloak answers **invalid username or password**.

The repository already knew: `deploy/compose/README.md` says to recreate the realm after editing
it. A demo whose first step is a sentence in a README somebody has to have read is a demo that
works for the person who wrote it. This is the same argument, and the same answer, as `vault-init`
one service over: **state the one-command demo depends on belongs in the stack, not in somebody's
memory.**

## What it will and will not do

It **re-imports the realm from the file** when the realm no longer matches it — deleting it first,
because Keycloak has no other way to apply a realm definition to an existing realm.

That is a destructive act, so it is bounded three ways, and none of them is a preference:

- **Environment.** Anything but `local` or `demo` and it does nothing at all. A real Keycloak is
  somebody's identity provider; the fact that a tool *can* delete a realm is exactly why it must
  ask where it is first (`ADR-0015`'s shape).
- **Evidence.** It re-imports only when something the file requires is absent — a demo user, a role
  group. A realm that already matches is left untouched, so this is idle on every normal start.
- **Never the product.** AIRA does not write to the directory (`FRD-209`); it *reads* group
  membership and that is the whole relationship. This is the demo stack provisioning its own
  development directory, which is a different thing wearing the same verb.

Uses `urllib` on purpose: it runs in a bare `python:*-slim` container with nothing installed, so
there is no image to build and no dependency to keep in step with anything.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")
REALM_FILE = os.environ.get("KEYCLOAK_REALM_FILE", "/realm/aira-realm.json")
ENVIRONMENT = os.environ.get("AIRA_ENVIRONMENT", "local")

#: Environments in which this realm is a development fixture rather than somebody's directory.
DEMO_ENVIRONMENTS = {"local", "demo"}


def _request(method: str, path: str, token: str | None = None, body: object = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{KEYCLOAK}{path}", data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed scheme
        payload = response.read()
    return json.loads(payload) if payload else None


def _token() -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "username": ADMIN,
            "password": ADMIN_PASSWORD,
            "grant_type": "password",
        }
    ).encode()
    request = urllib.request.Request(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token", data=form, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed scheme
        return json.loads(response.read())["access_token"]


def _missing(token: str, realm: dict) -> list[str]:
    """What the file requires and the running realm does not have.

    Users and groups only. Not a full comparison: a realm may legitimately carry more than the
    file (a person added themselves to test something), and re-importing over *that* would be a
    tool that punishes use. What is checked is what the demo promises — the accounts it prints and
    the groups those accounts get their roles from.
    """
    name = realm["realm"]
    try:
        users = _request("GET", f"/admin/realms/{name}/users?max=500", token) or []
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return ["the realm itself"]
        raise

    present_users = {u["username"] for u in users}  # type: ignore[index]
    # Service accounts are created **by their client**, not by the user list, and the user
    # endpoint does not return them. Comparing them made every realm look broken — the first
    # version of this check deleted a healthy realm on that evidence, which is the tool punishing
    # use that its own docstring warns about.
    wanted_users = {
        u["username"]
        for u in realm.get("users", [])
        if not u["username"].startswith("service-account-")
    }

    def paths(nodes: list[dict], prefix: str = "") -> set[str]:
        found: set[str] = set()
        for node in nodes:
            path = f"{prefix}/{node['name']}"
            found.add(path)
            found |= paths(node.get("subGroups", []), path)
        return found

    # Asked **one path at a time**, because `GET /groups` returns the top level and — since
    # Keycloak 26 — does not fill in `subGroups`. Reading the answer as "the nested groups are
    # gone" was the second half of the same false alarm. `group-by-path` answers exactly the
    # question being asked, and 404 is a real "no".
    missing_groups: list[str] = []
    for path in sorted(paths(realm.get("groups", []))):
        try:
            _request("GET", f"/admin/realms/{name}/group-by-path{path}", token)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            missing_groups.append(path)

    return sorted(
        [f"user {u}" for u in wanted_users - present_users] + [f"group {g}" for g in missing_groups]
    )


def main() -> int:
    if ENVIRONMENT not in DEMO_ENVIRONMENTS:
        print(f"environment '{ENVIRONMENT}' — this realm is somebody else's to manage")
        return 0

    realm = json.loads(open(REALM_FILE).read())  # noqa: SIM115, PTH123 - one read, no deps
    name = realm["realm"]

    token = _token()
    missing = _missing(token, realm)
    if not missing:
        print(f"realm '{name}' matches its definition")
        return 0

    print(f"realm '{name}' is missing: {', '.join(missing)}")
    print("re-importing it from the file — Keycloak skips the import when the realm exists, so a")
    print("realm created by an older checkout never sees a change made since.")
    try:
        _request("DELETE", f"/admin/realms/{name}", token)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise

    # **Wait for the delete to land before creating.** Keycloak answers the delete immediately and
    # finishes it afterwards, so a create posted straight away is overtaken by it: the script
    # reported `re-imported`, the API answered 201, and the realm was gone a second later. A
    # confident message about a realm that does not exist is worse than the stale realm it
    # replaced — the next person debugs the login instead of the import.
    for _ in range(60):
        try:
            _request("GET", f"/admin/realms/{name}", token)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                break
            raise
        time.sleep(0.5)
    else:
        print(f"realm '{name}' did not go away; not re-importing over it", file=sys.stderr)
        return 0

    _request("POST", "/admin/realms", token, realm)
    # Read it back. The one thing this must not do is announce a realm nobody can log in to.
    _request("GET", f"/admin/realms/{name}", token)
    print(f"realm '{name}' re-imported")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - a demo helper reports and does not stop the stack
        print(f"could not reconcile the realm: {error}", file=sys.stderr)
        # Deliberately not fatal. A realm that could not be checked is a reason to look, not a
        # reason to refuse to start the stack somebody is trying to look with.
        raise SystemExit(0) from error
