"""The realm repair notices a client the file defines and the running realm lacks (`FRD-614`).

Keycloak imports a realm only once, so a client added to the file later — the directory client
that checks a group before a role is bound to it — reaches no existing realm unless the repair
counts clients as well as users and groups.
"""

from __future__ import annotations

import importlib.util
import pathlib
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "keycloak_demo_realm", ROOT / "tools/keycloak_demo_realm.py"
)
assert _SPEC and _SPEC.loader
realm_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(realm_tool)

REALM = {
    "realm": "aira",
    "users": [],
    "groups": [],
    "clients": [{"clientId": "aira-gateway"}, {"clientId": "aira-directory"}],
}


def _keycloak(clients: list[str]):
    def request(method: str, path: str, token: str | None = None, body: object = None) -> object:
        if "/clients" in path:
            return [{"clientId": client} for client in clients]
        if "/group-by-path" in path:
            raise urllib.error.HTTPError(path, 404, "not found", {}, None)  # type: ignore[arg-type]
        return []

    return request


def test_a_client_the_file_defines_and_the_realm_lacks_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(realm_tool, "_request", _keycloak(["aira-gateway"]))
    assert realm_tool._missing("token", REALM) == ["client aira-directory"]


def test_a_realm_with_every_client_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(realm_tool, "_request", _keycloak(["aira-gateway", "aira-directory", "x"]))
    assert realm_tool._missing("token", REALM) == []
