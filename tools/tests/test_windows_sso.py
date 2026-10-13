"""The Windows sign-on's configuration, as `tools/windows_sso.py` makes it (`FRD-140`).

What the sign-on *does* is proven in a browser against a real domain (`make test-windows-sso`):
who gets into the console and the gateway with a Windows ticket, and who does not. These are the
properties of the configuration that suite stands on, checked without a stack — so that a change to
the tool that would quietly weaken one of them fails here, where every developer runs it, and not
only in the suite that needs a domain.

And the facts the domain and the tool must agree on, read from both: a realm named twice is a
sign-on that fails with no message.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import compose_files
import pytest
import stack_addresses
import windows_sso

ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "deploy" / "compose" / "ad" / "provision.sh"
REALM_FILE = ROOT / "deploy" / "compose" / "keycloak" / "realms" / "aira-realm.json"
SUITE = ROOT / "e2e" / "windows-sso"


@pytest.fixture(autouse=True)
def _no_developer_env(monkeypatch, tmp_path) -> None:
    """The stack's addresses from Compose's defaults, never from this machine's `.env`."""
    monkeypatch.setattr(stack_addresses, "ENV_FILE", tmp_path / ".env")
    for name in ("AIRA_OIDC_ISSUER", "AIRA_PUBLISH_KDC_PORT", "AIRA_BIND_HOST", "AIRA_STACK"):
        monkeypatch.delenv(name, raising=False)


def _federation(cache_lifespan: int = 300) -> dict[str, list[str]]:
    return windows_sso.federation("realm-id", "bind-password", cache_lifespan)["config"]


def _domain_service() -> dict:
    return compose_files.load(compose_files.SHOWCASE_ONLY)["services"]["ad"]


# == the realm's half ==============================================================================


def test_the_federation_never_writes_to_the_directory() -> None:
    """AIRA never writes to a directory (`FRD-209`), and the realm does not on its behalf:
    read-only, and nobody registered in the realm is created in AD."""
    config = _federation()

    assert config["editMode"] == ["READ_ONLY"]
    assert config["syncRegistrations"] == ["false"]


def test_kerberos_is_allowed_with_the_key_keycloak_is_given() -> None:
    config = _federation()

    assert config["allowKerberosAuthentication"] == ["true"]
    assert config["kerberosRealm"] == [windows_sso.KERBEROS_REALM]
    assert config["serverPrincipal"] == [windows_sso.service_principal()]
    assert config["keyTab"] == [windows_sso.KEYTAB_IN_KEYCLOAK]


def test_a_ticket_is_matched_by_account_name_not_by_principal_name() -> None:
    """A ticket names its person `<sAMAccountName>@<REALM>`, and a UPN ends in the mail domain in
    most installations — so a lookup by UPN finds nobody there. Empty, Keycloak matches the account
    name and checks the realm; Keycloak fills `userPrincipalName` in on creation, which is why
    `apply` edits the federation once more after creating it."""
    config = _federation()

    assert config["krbPrincipalAttribute"] == []
    assert config["usernameLDAPAttribute"] == ["sAMAccountName"]


def test_a_change_in_ad_reaches_the_realm_within_a_bound() -> None:
    """Keycloak's default cache keeps a person until something evicts them — a person disabled in
    AD renews their session, and one removed from a group keeps it in every new token. Bounded, the
    delay is the lifespan; Keycloak reads it in milliseconds."""
    config = _federation(cache_lifespan=42)

    assert config["cachePolicy"] == ["MAX_LIFESPAN"]
    assert config["maxLifespan"] == ["42000"]
    assert windows_sso.CACHE_LIFESPAN_SECONDS <= 300


def test_only_the_people_ou_is_read() -> None:
    """Who may sign on is scoped in AD: an account outside `OU=People` has a valid ticket and no
    way in. The whole domain as the search base would let every account of it sign on."""
    config = _federation()

    assert config["usersDn"] == [windows_sso.PEOPLE]
    assert windows_sso.PEOPLE != windows_sso.BASE_DN


def test_the_account_controls_mapper_reads_the_disabled_flag_every_time() -> None:
    """The mapper that keeps a person disabled in AD out — without it, a disabled person still signs
    on with a ticket issued before and still renews their session."""
    mapper = windows_sso.account_controls("parent")

    assert mapper["providerId"] == "msad-user-account-control-mapper"
    assert mapper["config"]["always.read.enabled.value.from.ldap"] == ["true"]


def test_groups_come_from_ad_read_only_and_land_where_aira_looks() -> None:
    """Role groups land under `/aira`, and every path `AIRA_ROLE_GROUPS` names is one a group of the
    domain lands on — so a person's AD group is their AIRA role with nothing in AIRA changed.
    Membership is AD's alone."""
    path = windows_sso.GROUP_PATHS[windows_sso.ROLES]
    roles = windows_sso.group_mapper("parent", windows_sso.ROLES, path)
    named = {pair.split("=", 1)[1] for pair in _compose_default("AIRA_ROLE_GROUPS").split(";")}
    landed = {
        f"{path}/{group}" for group in _domain_service()["environment"]["AD_ROLE_GROUPS"].split()
    }

    assert roles["config"]["mode"] == ["READ_ONLY"]
    assert roles["config"]["groups.path"] == [path]
    assert named == landed


def _compose_default(variable: str) -> str:
    for path in compose_files.ALL:
        match = re.search(rf"\$\{{{variable}:-([^}}]*)\}}", path.read_text())
        if match:
            return match.group(1)
    raise AssertionError(f"{variable} has no default in the Compose files")


# == the domain's half =============================================================================


def test_keycloaks_key_is_aes_only() -> None:
    """An account without AES is issued RC4 tickets, which Keycloak's Java refuses: the sign-on then
    fails into the form for everybody. DES and RC4 are the low three bits."""
    assert windows_sso.AES_ONLY & 0b111 == 0
    assert windows_sso.AES_ONLY & 0x18 == 0x18


def test_the_service_principal_names_the_host_of_the_issuer(monkeypatch) -> None:
    """A browser asks for a ticket to `HTTP/<the host in its address bar>`, and the console sends it
    to the issuer — so the principal follows the issuer, whatever host that is."""
    monkeypatch.setenv("AIRA_OIDC_ISSUER", "https://sso.firma.example/realms/aira")

    assert windows_sso.service_principal() == f"HTTP/sso.firma.example@{windows_sso.KERBEROS_REALM}"
    assert windows_sso.facts()["keycloakHost"] == "sso.firma.example"


def test_the_principal_is_not_on_the_bind_account() -> None:
    """Exporting a keytab on Windows resets the account's password; on the bind account that would
    stop the federation reading the directory."""
    assert f"CN={windows_sso.PRINCIPAL_ACCOUNT}," not in windows_sso.BIND_ACCOUNT


# == a workstation's Kerberos ======================================================================


def test_a_workstation_asks_for_the_host_exactly_as_typed() -> None:
    """MIT Kerberos would otherwise canonicalise the name or append a search domain to `localhost`,
    and ask the KDC for a principal nobody registered."""
    conf = windows_sso.workstation_krb5_conf()

    assert re.search(r"^\s*dns_canonicalize_hostname = false$", conf, re.M)
    assert re.search(r"^\s*rdns = false$", conf, re.M)
    assert re.search(r'^\s*qualify_shortname = ""$', conf, re.M)
    assert re.search(rf"^\s*localhost = {windows_sso.KERBEROS_REALM}$", conf, re.M)


def test_a_workstation_finds_the_kdc_where_it_is_published(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_PUBLISH_KDC_PORT", "19088")

    assert "kdc = localhost:19088" in windows_sso.workstation_krb5_conf()


# == what the domain and the tool must agree on ====================================================


def test_the_domain_and_the_tool_name_one_realm() -> None:
    environment = _domain_service()["environment"]
    realm = environment["AD_REALM"]

    assert realm == windows_sso.KERBEROS_REALM
    assert ",".join(f"DC={part}" for part in realm.lower().split(".")) == windows_sso.BASE_DN
    assert environment["AD_UPN_SUFFIX"] == windows_sso.UPN_SUFFIX


def test_the_domains_groups_are_the_realms_own() -> None:
    """A role group of the domain lands in the realm's group of the same name — the one
    `AIRA_ROLE_GROUPS` already names — so nothing in AIRA changes for the sign-on."""
    groups = {g["name"]: g for g in json.loads(REALM_FILE.read_text())["groups"]}
    environment = _domain_service()["environment"]

    def names(parent: str) -> set[str]:
        return {child["name"] for child in groups[parent].get("subGroups", [])}

    assert set(environment["AD_ROLE_GROUPS"].split()) == names("aira")
    assert set(environment["AD_DEPARTMENTS"].split()) == names("abteilungen")
    assert windows_sso.GROUP_PATHS == {
        windows_sso.ROLES: "/aira",
        windows_sso.DEPARTMENTS: "/abteilungen",
    }


def test_the_domain_makes_every_place_the_tool_reads() -> None:
    """The organisational units, the bind account and its secret: what provisioning makes is what
    the federation reads, or the realm's half fails against a domain that looks healthy."""
    script = PROVISION.read_text()

    for dn in (
        windows_sso.PEOPLE,
        windows_sso.ROLES,
        windows_sso.DEPARTMENTS,
        windows_sso.SERVICE_ACCOUNTS,
    ):
        ou = dn.split(",")[0].removeprefix("OU=")
        assert ou in script, ou
    assert windows_sso.BIND_ACCOUNT.split(",")[0].removeprefix("CN=") in script
    assert windows_sso.BIND_SECRET.removeprefix("/var/lib/samba/private/") in script


def test_keycloak_is_handed_the_key_the_domain_exported(monkeypatch) -> None:
    """The keytab goes from the domain controller to the path the federation names, as a file
    Keycloak alone may read — the bytes exported, unchanged."""
    calls: list[tuple[list[str], bytes | None]] = []

    def run(command, *, input=None, capture_output, check):  # noqa: A002 - subprocess's own name
        calls.append((command, input))
        from_the_domain = command[3] == stack_addresses.container("ad") and "cat" in command
        stdout = b"\x05\x02 the key" if from_the_domain else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(windows_sso.subprocess, "run", run)

    windows_sso.hand_the_keytab_to_keycloak()

    (read, _), (write, given) = calls
    assert read[-2:] == ["cat", windows_sso.KEYTAB_ON_THE_DOMAIN]
    assert read[3] == stack_addresses.container("ad")
    assert write[3] == stack_addresses.container("keycloak")
    assert write[-1] == windows_sso.KEYTAB_IN_KEYCLOAK == _federation()["keyTab"][0]
    assert "umask 077" in write[-3]
    assert given == b"\x05\x02 the key"


def test_every_fact_the_browser_suite_reads_is_one_the_tool_gives() -> None:
    """The suite asks the tool for the domain's facts rather than writing them down; a name it reads
    that the tool does not give is `undefined`, and the test that uses it fails about something
    else."""
    read = {
        name
        for path in SUITE.glob("*.ts")
        for name in re.findall(r"\bDOMAIN\.(\w+)", path.read_text())
    }

    assert read, "the suite reads no fact at all; the search is wrong"
    assert read <= set(windows_sso.facts()), read - set(windows_sso.facts())


# == only where the realm is a fixture =============================================================


def test_a_realm_that_is_somebodys_directory_is_left_alone(monkeypatch, capsys) -> None:
    """Like the realm repair (`tools/keycloak_demo_realm.py`): anything but a local or demo
    environment, and nothing is written."""
    monkeypatch.setenv("AIRA_ENVIRONMENT", "production")
    touched: list[str] = []
    monkeypatch.setattr(windows_sso, "apply", lambda *_: touched.append("apply"))
    monkeypatch.setattr(windows_sso, "remove", lambda: touched.append("remove"))

    assert windows_sso.main(["apply"]) == 1
    assert windows_sso.main(["remove"]) == 1
    assert touched == []
    assert "somebody else's" in capsys.readouterr().out
