"""The doctor's one heuristic, against the names this repository actually creates.

`showcase_doctor` reports a use case, a key or an account that is wrong. Its finding about a
**duplicated account** is the only one that is inferred from a *name* rather than read from a
table, and it was inferred from the wrong property: any username whose last hyphen-separated
segment was eight characters long. The shipped realm creates
`service-account-aira-integration-tests-security` — last segment `security`, eight letters — so
`make showcase-doctor` exited 1 on a healthy stack and printed *"1 thing(s) to fix"* about an
account the repository ships. Every run, for everybody.

`LESSONS.md` §3: **a check that cries wolf on the supported path is one nobody reads on the day it
is right.** A section whose one finding is always wrong is a section its reader learns to skip, and
it is the section that would name a real duplicate.

The real signature is `f"{preferred}-{subject[:8]}"` (`apps/api/authentication.py`) — the first
eight characters of a directory **UUID**. So the check is about hexadecimal, and this file holds it
to both halves: it must not fire on any name this repository creates, and it must still fire on the
name Management invents.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import showcase_doctor  # noqa: E402
from showcase_doctor import is_duplicated_username  # noqa: E402

REALM = ROOT / "deploy" / "compose" / "keycloak" / "realms" / "aira-realm.json"


def _realm_usernames() -> list[str]:
    """Every account the shipped realm creates, including the service accounts.

    Read from the realm file rather than listed here, so an account added tomorrow is covered on
    the day it is added — which is the property the check itself failed to have.
    """
    realm = json.loads(REALM.read_text())
    names = [user["username"] for user in realm.get("users", []) if user.get("username")]
    # A client with a service account produces `service-account-<clientId>`, which is the shape
    # that tripped this: it is the longest name in the realm and the one nobody types.
    names += [
        f"service-account-{client['clientId']}"
        for client in realm.get("clients", [])
        if client.get("serviceAccountsEnabled")
    ]
    return names


def test_the_realm_actually_has_accounts_to_check() -> None:
    """A guard on the guard: an empty list would make the check below pass by finding nothing."""
    names = _realm_usernames()
    assert len(names) > 5
    assert "service-account-aira-integration-tests-security" in names, (
        "the account that tripped the old heuristic is gone — if it moved, move this check with it"
    )


@pytest.mark.parametrize("username", _realm_usernames())
def test_no_account_this_repository_creates_is_reported_as_a_duplicate(username: str) -> None:
    assert not is_duplicated_username(username), (
        f"the doctor reports '{username}' as a duplicated account, and this repository creates it. "
        "A finding that is always wrong is a section its reader learns to skip."
    )


@pytest.mark.parametrize(
    "username",
    [
        "ucadmin-1361bd47",
        "admin-00b1191e",
        "service-account-aira-integration-tests-cfbf3f91",
        "alice-deadbeef",
    ],
)
def test_the_name_management_invents_for_a_changed_subject_is_still_reported(username: str) -> None:
    """The other half. Narrowing a check that cried wolf must not silence it — `authentication.py`
    appends `subject[:8]`, and that is what a real rebinding leaves behind."""
    assert is_duplicated_username(username)


# --- the login chain, which is read on somebody else's machine ----------------------------------
#
# Reported from use, twice in one session: `keycloak not reachable` on the console while every
# server-side check the doctor made was green. It was green *and correct* — the doctor walked
# container to container, and the login is walked by a browser. These cover the parsing the new
# section does; the sentence it prints is what the section is actually for.


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            "window.__AIRA_CONFIG__ = {\n  issuer: 'http://kc:8080/realms/aira',\n};",
            "http://kc:8080/realms/aira",
        ),
        ("issuer: 'https://sso.example.com/realms/aira',", "https://sso.example.com/realms/aira"),
        ("window.__AIRA_CONFIG__ = {};", ""),
    ],
)
def test_the_issuer_is_read_out_of_the_served_runtime_config(config: str, expected: str) -> None:
    assert showcase_doctor._between(config, "issuer: '", "'") == expected


@pytest.mark.parametrize(
    ("issuer", "origin"),
    [
        ("http://localhost:8080/realms/aira", "http://localhost:8080"),
        ("https://sso.example.com/realms/aira", "https://sso.example.com"),
        ("", ""),
        ("not-a-url", ""),
    ],
)
def test_the_origin_is_what_a_content_policy_would_name(issuer: str, origin: str) -> None:
    """A policy names `scheme://host:port` and never a path, so comparing the issuer itself would
    never match and the check would report a mismatch on every healthy stack."""
    assert showcase_doctor._origin(issuer) == origin


def test_the_connect_directive_is_picked_out_of_the_whole_policy() -> None:
    policy = "default-src 'self'; connect-src 'self' http://localhost:8080; img-src 'self' data:"
    assert (
        showcase_doctor._directive(policy, "connect-src")
        == "connect-src 'self' http://localhost:8080"
    )
    assert showcase_doctor._directive(policy, "frame-src") == ""


def test_a_policy_that_does_not_name_the_issuer_is_a_mismatch() -> None:
    """The failure with no trace on the server side at all: the browser refuses the token request,
    and Keycloak never sees one. It is the case `AIRA_CSP_CONNECT_SRC` exists for."""
    issuer = "http://sso.example.com:8080/realms/aira"
    policy = "connect-src 'self' http://localhost:8080"
    assert showcase_doctor._origin(issuer) not in showcase_doctor._directive(policy, "connect-src")


def test_a_policy_that_names_it_is_not() -> None:
    issuer = "http://localhost:8080/realms/aira"
    policy = "connect-src 'self' http://localhost:8080"
    assert showcase_doctor._origin(issuer) in showcase_doctor._directive(policy, "connect-src")


def test_a_missing_header_is_not_read_as_an_allowed_origin() -> None:
    """An absent policy must not pass the check by accident: `"" in ""` is True in Python, and a
    stack serving no CSP at all would then be reported as allowing whatever it was asked about."""
    assert showcase_doctor._directive("", "connect-src") == ""
    assert not (showcase_doctor._origin("") and showcase_doctor._origin("") in "")
