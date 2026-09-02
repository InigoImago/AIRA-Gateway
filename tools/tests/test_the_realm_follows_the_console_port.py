"""Moving the console's port moves the login with it.

`AIRA_PUBLISH_FRONTEND_PORT` was introduced on 2026-08-18 so a second system on the same machine
could not force an edit to the Compose file, and `docs/SETUP.md` said *"every published port is a
variable"*. For the console that was **not true**, and the way it was untrue is the worst kind:
`keycloak/realms/aira-realm.json` pinned the client's `redirectUris` and `webOrigins` to a literal
`4200`, so moving the port left a console that loads perfectly and a login that fails with
*"Invalid parameter: redirect_uri"* — an error naming the realm, not the port, on a screen the
reader did not change.

A knob that silently breaks authentication is worse than no knob. Keycloak substitutes
`${VAR:default}` when importing a realm — measured against 26.1 with this very file, both with the
variable set (`14200` came out) and unset (`4200` came out) — so the realm now follows the port.

Two statements have to agree for that to keep working: the placeholder in the realm, and the
`AIRA_CONSOLE_PORT` Compose resolves for the Keycloak container. **The fallback chain is resolved
in Compose**, because the realm's syntax takes one name and one default while Compose's takes a
chain, and half an expression of one rule in each place is how they drift. This compares them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = ROOT / "deploy" / "compose"
REALM = COMPOSE_DIR / "keycloak" / "realms" / "aira-realm.json"
INFRA = COMPOSE_DIR / "docker-compose.yml"
APPS = COMPOSE_DIR / "docker-compose.apps.yml"

#: The placeholder the realm uses, and the variable Compose must therefore set.
PLACEHOLDER = "${AIRA_CONSOLE_PORT:4200}"
VARIABLE = "AIRA_CONSOLE_PORT"

#: And the **host** half, added 2026-09-02 for the same reason one step later. Moving the port was
#: made possible and moving the machine was not: the realm pinned `localhost` and `127.0.0.1`, so a
#: stack reached from anywhere else failed at the redirect — *after* the operator had already found
#: `AIRA_BIND_HOST` and `AIRA_OIDC_ISSUER`, which is two fixes deep before a third wall that gives
#: the same `server could not be reached`.
HOST_PLACEHOLDER = "${AIRA_CONSOLE_HOST:localhost}"
HOST_VARIABLE = "AIRA_CONSOLE_HOST"

#: The console's published port, as Compose writes it. Both names, outermost first — the inner one
#: predates the `AIRA_PUBLISH_` family and `docs/SETUP.md` documented it for months.
PUBLISHED_CHAIN = "${AIRA_PUBLISH_FRONTEND_PORT:-${AIRA_FRONTEND_PORT:-4200}}"

#: What Keycloak is told, which is the port **in the browser's address bar**. It defaults to the
#: published one and can be named on its own, because a forwarder in front of the machine remaps
#: them: the container serves 4200, the browser says 14200, and Keycloak compares against the
#: browser. That case is not exotic — it is what somebody does when another system holds the port.
BROWSER_CHAIN = "${AIRA_CONSOLE_PORT:-" + PUBLISHED_CHAIN + "}"


def _client() -> dict:
    """The console's OIDC client, from the realm this stack imports."""
    text = REALM.read_text().replace(PLACEHOLDER, "4200").replace(HOST_PLACEHOLDER, "localhost")
    realm = json.loads(text)
    for client in realm["clients"]:
        if client.get("clientId") == "aira-gateway":
            return client
    raise AssertionError("the realm has no `aira-gateway` client")


def test_the_realm_still_describes_the_console_client() -> None:
    """A guard on the guard: no client means every assertion below is about nothing."""
    client = _client()

    assert client["redirectUris"], client
    assert client["webOrigins"], client


def test_the_realm_names_no_literal_console_port() -> None:
    """The defect itself. A literal here is a port that cannot be moved, and the symptom is a
    login failure that names the realm rather than the port."""
    literals = [
        line.strip()
        for line in REALM.read_text().splitlines()
        if re.search(r"https?://(?:localhost|127\.0\.0\.1):\d+", line)
    ]

    assert not literals, (
        "The realm pins a port instead of following one:\n  "
        + "\n  ".join(literals)
        + f"\n\nUse {PLACEHOLDER}; Keycloak substitutes it on import."
    )


def test_every_console_url_in_the_realm_uses_the_placeholder() -> None:
    """The other direction: a URI added without the placeholder is the defect coming back on a
    line nobody looked at, and the two lists here are edited together far more often than read."""
    client = _client()
    urls = [*client["redirectUris"], *client["webOrigins"]]
    source = REALM.read_text()

    assert len(urls) >= 4, urls
    assert source.count(PLACEHOLDER) == len(urls), (
        f"{len(urls)} console URLs in the realm and {source.count(PLACEHOLDER)} placeholders — "
        "one of them names a fixed port"
    )


def test_compose_hands_keycloak_the_resolved_port() -> None:
    """Keycloak can only substitute a variable its container has.

    Without this line the placeholder falls back to its own default and the realm silently pins
    4200 again — the original defect, wearing the fix's clothes.
    """
    infra = INFRA.read_text()

    assert f"{VARIABLE}: {BROWSER_CHAIN}" in infra, (
        f"the Keycloak service must set `{VARIABLE}: {BROWSER_CHAIN}` — the published port, "
        "overridable on its own for a forwarded deployment, resolved once here because the realm's "
        "syntax cannot express a chain"
    )


def test_the_two_chains_are_the_same_chain() -> None:
    """What Compose publishes the console on, and what it tells Keycloak, are one decision.

    Written out twice because they live in two files that Compose merges; compared here because a
    reader changing one has no way to see the other, and the failure is a login screen.
    """
    published = [
        line.strip()
        for line in APPS.read_text().splitlines()
        if "AIRA_PUBLISH_FRONTEND_PORT" in line and ":8080" in line
    ]

    # **Two entries, one per address family.** Docker opens one socket per published entry, so the
    # console is published on IPv4 and on IPv6 — and both have to name the same port, or half the
    # callers reach a different one than Keycloak was told about.
    assert len(published) == 2, published
    for entry in published:
        assert PUBLISHED_CHAIN in entry, (
            f"the console is published on {entry!r}, and Keycloak is told "
            f"{BROWSER_CHAIN} — one of them moved"
        )


# --- and the host, which is the same rule one step out ------------------------------------------


def test_the_realm_offers_a_host_that_is_not_this_machine() -> None:
    """A console reached from another machine has to be a redirect URI, or Keycloak refuses it.

    The URIs are pinned rather than wildcarded on purpose — a wildcard on a public client lets an
    attacker capture the authorization code (`ADR-0007`) — so the answer is a *named* host, not a
    looser pattern.
    """
    source = REALM.read_text()

    assert HOST_PLACEHOLDER in source, (
        f"the realm names only this machine, so a console at any other address cannot log in. "
        f"Use {HOST_PLACEHOLDER}; Keycloak substitutes it on import."
    )


def test_loopback_still_works_whatever_the_host_is_set_to() -> None:
    """The laptop must not pay for the deployment. `localhost` and `127.0.0.1` stay listed in their
    own right, so setting the host **adds** an address and never swaps one out."""
    client = _client()
    urls = [*client["redirectUris"], *client["webOrigins"]]

    assert any(u.startswith("http://localhost:") for u in urls), urls
    assert any(u.startswith("http://127.0.0.1:") for u in urls), urls


def test_compose_hands_keycloak_the_console_host() -> None:
    """Keycloak can only substitute a variable its container has — the port's own lesson, and the
    one that makes this knob real rather than a placeholder nothing fills in."""
    infra = INFRA.read_text()

    assert f"{HOST_VARIABLE}: ${{{HOST_VARIABLE}:-localhost}}" in infra, (
        f"the Keycloak service must set `{HOST_VARIABLE}`, or the placeholder falls back to its "
        "own default and the realm silently names only this machine again"
    )


def test_both_schemes_are_offered_for_a_named_host() -> None:
    """A stack behind a TLS-terminating proxy is reached at `https://`, and the browser's scheme is
    what Keycloak compares. Naming only `http://` there would move the same failure one step on."""
    source = REALM.read_text()

    assert f"http://{HOST_PLACEHOLDER}" in source, source
    assert f"https://{HOST_PLACEHOLDER}" in source, source
