"""A directory account kept in Keycloak's master realm reads the realm it serves (`FRD-209` §3.1).

Many installations keep their administrative service accounts in `master` and give one of them
`view-users` and `query-groups` on their realm through master's `<realm>-realm` client.
`AIRA_DIRECTORY_TOKEN_REALM=master` asks master for that account's token, and every lookup still
reads the issuer's realm.

The hermetic suite answers Keycloak with a stand-in, and a stand-in agrees with the model it was
written from. Two facts only a real Keycloak can state: that a token master issued is accepted on
`/admin/realms/<realm>/…`, and that the realm refuses the account when the token is asked of it —
`401 invalid_client`, the failure that sent the console's search back to Management's own answer.
So this makes such an account the way an installation does, and asks the directory client both
ways.

It writes to the master realm, which nothing else here does. The account is removed whatever the
test did, and one a run left behind when it died is removed by the next run, by its prefix.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest

from aira_common.access import SubjectKind
from aira_common.directory import (
    TOKEN_STEP,
    DirectoryUnavailable,
    KeycloakDirectory,
    directory_for,
)

from .keycloak import KEYCLOAK_URL, REALM, admin_headers_sync, created_id

pytestmark = pytest.mark.integration

#: Where the account lives, as `AIRA_DIRECTORY_TOKEN_REALM` names it.
TOKEN_REALM = "master"
#: The client master keeps for the realm this stack serves; its roles are what the account holds.
REALM_CLIENT = f"{REALM}-realm"
#: Exactly what the guide tells an installation to grant: reading, on one realm.
READ_ROLES = ("view-users", "query-groups")
#: Every account this file makes starts with it, so a run that died can be cleaned up by name.
PREFIX = "itest-directory-"
#: A group and a person the development realm holds — and master does not.
GROUP = "/aira/global-admins"
USER = "ucadmin"

MASTER = f"{KEYCLOAK_URL}/admin/realms/{TOKEN_REALM}"


def _remove_leftovers(client: httpx.Client, headers: dict[str, str]) -> None:
    listed = client.get(
        f"{MASTER}/clients", params={"clientId": PREFIX, "search": "true"}, headers=headers
    )
    assert listed.status_code == 200, listed.text
    for leftover in listed.json():
        if leftover["clientId"].startswith(PREFIX):
            client.delete(f"{MASTER}/clients/{leftover['id']}", headers=headers)


@pytest.fixture(scope="module")
def master_account() -> Iterator[tuple[str, str]]:
    """A confidential client in master whose service account reads this realm, and nothing else."""
    client_id = f"{PREFIX}{uuid.uuid4().hex[:8]}"
    with httpx.Client(timeout=30.0) as client:
        headers = admin_headers_sync(client)
        _remove_leftovers(client, headers)
        made = client.post(
            f"{MASTER}/clients",
            json={
                "clientId": client_id,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
            },
            headers=headers,
        )
        internal = created_id(made)
        try:
            secret = client.get(f"{MASTER}/clients/{internal}/client-secret", headers=headers)
            assert secret.status_code == 200, secret.text
            account = client.get(
                f"{MASTER}/clients/{internal}/service-account-user", headers=headers
            )
            assert account.status_code == 200, account.text
            realm_client = client.get(
                f"{MASTER}/clients", params={"clientId": REALM_CLIENT}, headers=headers
            )
            assert realm_client.status_code == 200 and realm_client.json(), realm_client.text
            realm_client_id = realm_client.json()[0]["id"]
            roles = []
            for role in READ_ROLES:
                found = client.get(
                    f"{MASTER}/clients/{realm_client_id}/roles/{role}", headers=headers
                )
                assert found.status_code == 200, found.text
                roles.append(found.json())
            granted = client.post(
                f"{MASTER}/users/{account.json()['id']}/role-mappings/clients/{realm_client_id}",
                json=roles,
                headers=headers,
            )
            assert granted.status_code == 204, granted.text
            yield client_id, secret.json()["value"]
        finally:
            # A fresh token: master's admin tokens live a minute, and the tests may take longer.
            client.delete(f"{MASTER}/clients/{internal}", headers=admin_headers_sync(client))


def test_a_token_asked_of_master_reads_the_realm_the_issuer_names(
    master_account: tuple[str, str],
) -> None:
    client_id, secret = master_account
    directory = KeycloakDirectory(KEYCLOAK_URL, REALM, client_id, secret, token_realm=TOKEN_REALM)

    assert directory.group_exists(GROUP)
    assert any(
        entry.kind is SubjectKind.GROUP and entry.id == GROUP
        for entry in directory.search("global-admins")
    )
    person = directory.find_user(USER)
    assert person is not None and person.id == USER


def test_both_planes_build_the_same_reader_from_the_issuer_and_the_setting(
    master_account: tuple[str, str],
) -> None:
    """`directory_for` is what Management's search and the gateway's mail both call: the issuer
    names the realm read and the server, the setting the realm the token comes from."""
    client_id, secret = master_account
    directory = directory_for(
        f"{KEYCLOAK_URL}/realms/{REALM}", "", client_id, secret, token_realm=TOKEN_REALM
    )

    assert directory is not None
    assert directory.group_exists(GROUP)


def test_without_the_setting_the_realm_refuses_the_account_and_says_where(
    master_account: tuple[str, str],
) -> None:
    """The failure an installation meets before it sets `AIRA_DIRECTORY_TOKEN_REALM`, as the
    warning `directory.unavailable` reports it: the token step, the realm asked, `401`."""
    client_id, secret = master_account
    directory = KeycloakDirectory(KEYCLOAK_URL, REALM, client_id, secret)

    with pytest.raises(DirectoryUnavailable) as refused:
        directory.search("global-admins")

    assert (refused.value.step, refused.value.realm, refused.value.status) == (
        TOKEN_STEP,
        REALM,
        401,
    )


def test_the_account_reads_the_realm_and_changes_nothing_in_it(
    master_account: tuple[str, str],
) -> None:
    """An account in master can be given roles on every realm of the server, which is why the
    guide says to give it these two on one realm. Given just those, it can read and not write."""
    client_id, secret = master_account
    with httpx.Client(timeout=30.0) as client:
        token = client.post(
            f"{KEYCLOAK_URL}/realms/{TOKEN_REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        assert token.status_code == 200, token.text
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

        read = client.get(f"{KEYCLOAK_URL}/admin/realms/{REALM}/groups", headers=headers)
        written = client.post(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/groups",
            json={"name": f"{PREFIX}{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        elsewhere = client.get(f"{MASTER}/users", headers=headers)

    assert read.status_code == 200, read.text
    assert written.status_code == 403, written.text
    assert elsewhere.status_code == 403, elsewhere.text
