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
