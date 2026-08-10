"""The demo realm's identities are fixtures, so they must not move.

Keycloak imports a realm **only if it does not exist**. Every edit to the realm file therefore
reaches a fresh machine and no other, and a colleague whose realm predates a change keeps the old
one for ever — reported as `make showcase` printing five accounts that Keycloak answers *invalid
username or password* to.

`tools/keycloak_demo_realm.py` repairs that by re-importing the realm. Which is safe **only**
because the ids are pinned in the file: Keycloak's `sub` is the user id, `ADR-0007` binds a Django
user to that `sub`, and an unpinned re-import mints new ones. Measured, not feared — the first
attempt logged in as `ucadmin` and the console said `ucadmin-279b6b7b`, because Management had
auto-provisioned a *second* user for a subject it had never seen and avoided the username
collision. The repair had fixed the directory and corrupted the thing that reads it.

Same rule as `FRD-130`'s deterministic demo keys, one identity system over: **a demo whose fixtures
change is a demo whose examples stop working the second time somebody runs it.**
"""

from __future__ import annotations

import json
import pathlib

REALM = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deploy"
    / "compose"
    / "keycloak"
    / "realms"
    / "aira-realm.json"
)


def _realm() -> dict:
    return json.loads(REALM.read_text())


def test_every_demo_user_has_a_stable_id() -> None:
    unpinned = [u["username"] for u in _realm().get("users", []) if not u.get("id")]

    assert not unpinned, (
        f"{unpinned} would get a new subject on every re-import, and every Django user bound to "
        "the old one becomes a stranger — the console then shows a suffixed duplicate"
    )


def test_every_group_has_a_stable_id() -> None:
    """Group ids matter for the same reason one step out: a grant is guardian rows against a
    Django group mirroring a Keycloak path, and a re-import that renumbers the groups leaves the
    paths intact but nothing that pointed at them."""

    def unpinned(nodes: list[dict], prefix: str = "") -> list[str]:
        found: list[str] = []
        for node in nodes:
            path = f"{prefix}/{node['name']}"
            if not node.get("id"):
                found.append(path)
            found += unpinned(node.get("subGroups", []), path)
        return found

    missing = unpinned(_realm().get("groups", []))

    assert not missing, f"{missing} would be renumbered by a re-import"


def test_the_ids_are_unique() -> None:
    """A copied id would silently merge two identities, which is the failure this file is about
    with the sign reversed."""

    def ids(nodes: list[dict]) -> list[str]:
        found = []
        for node in nodes:
            found.append(node["id"])
            found += ids(node.get("subGroups", []))
        return found

    realm = _realm()
    everything = [u["id"] for u in realm.get("users", [])] + ids(realm.get("groups", []))

    assert len(everything) == len(set(everything))


def test_the_reconciler_refuses_anything_but_a_development_realm() -> None:
    """It deletes a realm to re-import it. The one guarantee that has to hold is *where*."""
    source = (REALM.parents[4] / "tools" / "keycloak_demo_realm.py").read_text()

    assert 'DEMO_ENVIRONMENTS = {"local", "demo"}' in source
    assert "if ENVIRONMENT not in DEMO_ENVIRONMENTS:" in source
    # And it must not delete on a whim: only when the file names something the realm lacks.
    assert "if not missing:" in source
