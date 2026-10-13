"""Windows sign-on for the development realm — both administrators' halves, as code (`FRD-140`).

A Windows sign-on is Kerberos (SPNEGO) between the browser and **Keycloak**: the workstation
presents the ticket it was given when the person logged on, Keycloak decrypts it with its own key,
finds the person in the directory and issues the tokens a password would have produced. AIRA is not
in that exchange and changes nothing for it (`ADR-0035`). What changes is configuration, in two
places owned by two different people:

- **the domain's** — a service principal `HTTP/<the host browsers use for Keycloak>` on an account
  of its own, AES keys for it, and its keytab exported for Keycloak;
- **the realm's** — the directory as a user federation with Kerberos allowed, which also turns the
  browser flow's *Kerberos* step to ALTERNATIVE.

This makes both against the stand-in domain of the development stack (`deploy/compose/ad`, profile
`windows-sso`), so that the sign-on can be proven end to end by `make test-windows-sso`. It is also
the most exact statement of the change an installation makes: `docs/INTEGRATIONS.md` §2, *Windows
sign-on*, walks through the same steps for a real AD and a real Keycloak.

    python3 tools/windows_sso.py apply [--cache-lifespan SECONDS]
    python3 tools/windows_sso.py remove
    python3 tools/windows_sso.py keytab        # hand Keycloak its key again, as after a reset
    python3 tools/windows_sso.py facts         # what the browser suite needs to know, as JSON
    python3 tools/windows_sso.py workstation   # a krb5.conf for a browser standing in for a PC

Standard library only, like `stack_addresses.py`: the Makefile and the browser suite run it with
the system `python3`, before any virtual environment exists.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import stack_addresses

#: The development domain. Compose hands the same facts to its controller (the `ad` service), and
#: `tools/tests/test_windows_sso.py` fails when the two copies disagree.
KERBEROS_REALM = "AIRA.TEST"
BASE_DN = "DC=aira,DC=test"
PEOPLE = f"OU=People,OU=AIRA,{BASE_DN}"
ROLES = f"OU=Roles,OU=AIRA,{BASE_DN}"
DEPARTMENTS = f"OU=Departments,OU=AIRA,{BASE_DN}"
SERVICE_ACCOUNTS = f"OU=Service accounts,OU=AIRA,{BASE_DN}"
#: The suffix of people's user principal names — the mail domain, as in most installations, and
#: not the realm their tickets are issued in.
UPN_SUFFIX = "firma.example"

#: Where each part of the directory lands among the realm's groups. Role groups land beside the
#: realm's own role groups, so `AIRA_ROLE_GROUPS` needs no change; departments land where the
#: realm keeps departments, so a use case is granted to one exactly as to any other group.
GROUP_PATHS = {ROLES: "/aira", DEPARTMENTS: "/abteilungen"}

#: The account Keycloak reads the directory with — what an installation's federation already has.
BIND_ACCOUNT = f"CN=svc-keycloak-ldap,{SERVICE_ACCOUNTS}"
BIND_SECRET = "/var/lib/samba/private/keycloak-bind.secret"
#: The account that holds Keycloak's service principal. Its own, not the bind account: exporting a
#: keytab on Windows (`ktpass … -pass`) resets the account's password, and a reset bind password
#: stops the federation reading the directory at all.
PRINCIPAL_ACCOUNT = "svc-keycloak-http"
#: AES128 and AES256, and nothing older. An account without this attribute is issued RC4 tickets,
#: and Keycloak's Java refuses RC4 by default: everybody's sign-on then falls back to the form.
AES_ONLY = 0x18

#: Where the keytab is exported to on the domain controller, and where Keycloak is given it.
KEYTAB_ON_THE_DOMAIN = "/var/lib/samba/private/keycloak.keytab"
KEYTAB_IN_KEYCLOAK = "/opt/keycloak/conf/keycloak.keytab"

REALM = "aira"
FEDERATION = "windows-domain"
STORAGE = "org.keycloak.storage.UserStorageProvider"
MAPPER = "org.keycloak.storage.ldap.mappers.LDAPStorageMapper"
ACCOUNT_CONTROLS = "msad-user-account-control-mapper"

#: How long Keycloak may answer from its own copy of a person before it asks the directory again.
#:
#: **This is how long a change in AD takes to reach AIRA**, and Keycloak's default has no bound at
#: all: its user cache keeps a person until something evicts them, so a person disabled in AD can
#: go on renewing their session, and a person removed from a group goes on receiving it in their
#: token — on a fresh Kerberos sign-on too. A bounded lifespan makes the delay a number: access
#: ends at most this long, plus one access-token lifetime, after the change in AD.
CACHE_LIFESPAN_SECONDS = 300

#: Environments in which this realm is a development fixture rather than somebody's directory.
DEMO_ENVIRONMENTS = {"local", "demo"}


def keycloak_host() -> str:
    """The host a browser uses for Keycloak, which is the host of the issuer the console trusts.

    The service principal names this host and nothing else: a browser asks the KDC for a ticket to
    `HTTP/<the host in the address bar>`, so a principal for any other name is one no browser ever
    requests. One owner — the issuer — because the console redirects to it.
    """
    host = urllib.parse.urlsplit(stack_addresses.issuer()).hostname
    if not host:
        raise ValueError(f"the issuer {stack_addresses.issuer()!r} names no host")
    return host


def service_principal() -> str:
    return f"HTTP/{keycloak_host()}@{KERBEROS_REALM}"


def federation(realm_id: str, bind_password: str, cache_lifespan: int) -> dict:
    """The directory as Keycloak's user federation, with Kerberos allowed.

    Read-only, and registering nobody: AIRA never writes to a directory (`FRD-209`), and neither
    does the realm on its behalf. A person signs on with a ticket or, where the browser has none,
    with their domain password through the same federation — that second path is what an
    installation federating its AD already has.

    **The Kerberos principal attribute is empty on purpose.** A ticket names its person
    `<sAMAccountName>@<REALM>`; filled with `userPrincipalName`, Keycloak looks for a UPN of exactly
    that, and finds nobody wherever the UPN suffix is the mail domain rather than the realm —
    which is most installations. Empty, it matches the account name and checks the realm.
    """
    return {
        "name": FEDERATION,
        "providerId": "ldap",
        "providerType": STORAGE,
        "parentId": realm_id,
        "config": {
            "enabled": ["true"],
            "vendor": ["ad"],
            "connectionUrl": ["ldap://ad:389"],
            "authType": ["simple"],
            "bindDn": [BIND_ACCOUNT],
            "bindCredential": [bind_password],
            "usersDn": [PEOPLE],
            "searchScope": ["2"],
            "userObjectClasses": ["person, organizationalPerson, user"],
            "usernameLDAPAttribute": ["sAMAccountName"],
            "rdnLDAPAttribute": ["cn"],
            "uuidLDAPAttribute": ["objectGUID"],
            "editMode": ["READ_ONLY"],
            "syncRegistrations": ["false"],
            "importEnabled": ["true"],
            "pagination": ["true"],
            "cachePolicy": ["MAX_LIFESPAN"],
            "maxLifespan": [str(cache_lifespan * 1000)],
            "allowKerberosAuthentication": ["true"],
            "kerberosRealm": [KERBEROS_REALM],
            "serverPrincipal": [service_principal()],
            "keyTab": [KEYTAB_IN_KEYCLOAK],
            "krbPrincipalAttribute": [],
            "useKerberosForPasswordAuthentication": ["false"],
        },
    }


def account_controls(parent_id: str) -> dict:
    """AD's own *account disabled* flag, read from the directory whenever the person is.

    **What keeps a person disabled in AD out.** Nothing else reads `userAccountControl`: without
    this mapper a disabled person still signs on with a ticket issued before, and still renews
    their session. Keycloak creates it for a federation whose vendor is Active Directory and for
    no other, so it is made sure of here rather than assumed.
    """
    return {
        "name": "MSAD account controls",
        "providerId": ACCOUNT_CONTROLS,
        "providerType": MAPPER,
        "parentId": parent_id,
        "config": {"always.read.enabled.value.from.ldap": ["true"]},
    }


def group_mapper(parent_id: str, groups_dn: str, path: str) -> dict:
    """The groups under ``groups_dn`` as the realm's groups under ``path``, membership from AD.

    Read-only like the federation: a membership is changed in AD and nowhere else, so the group a
    person is in is the group the directory says, and the token says the same.
    """
    return {
        "name": f"groups {path}",
        "providerId": "group-ldap-mapper",
        "providerType": MAPPER,
        "parentId": parent_id,
        "config": {
            "groups.dn": [groups_dn],
            "groups.path": [path],
            "group.name.ldap.attribute": ["cn"],
            "group.object.classes": ["group"],
            "membership.ldap.attribute": ["member"],
            "membership.attribute.type": ["DN"],
            "membership.user.ldap.attribute": ["sAMAccountName"],
            "user.roles.retrieve.strategy": ["LOAD_GROUPS_BY_MEMBER_ATTRIBUTE"],
            "memberof.ldap.attribute": ["memberOf"],
            "preserve.group.inheritance": ["false"],
            "drop.non.existing.groups.during.sync": ["false"],
            "mode": ["READ_ONLY"],
        },
    }


def workstation_krb5_conf() -> str:
    """Kerberos for a workstation of the domain — the browser standing in for one.

    **The service principal is built from the host exactly as typed.** A browser asks for
    `HTTP@<host>`, and MIT Kerberos would otherwise rewrite the name before asking: canonicalise it
    through DNS, or append the machine's first search domain to a name without a dot
    (`localhost.<search domain>`) — a principal nobody registered, which the KDC answers with
    *server not found*. Windows does not rewrite the name either, so this is the workstation's
    behaviour, not a test convenience.
    """
    host = keycloak_host()
    return f"""\
[libdefaults]
    default_realm = {KERBEROS_REALM}
    dns_lookup_kdc = false
    dns_lookup_realm = false
    dns_canonicalize_hostname = false
    rdns = false
    qualify_shortname = ""
    udp_preference_limit = 1
[realms]
    {KERBEROS_REALM} = {{
        kdc = {stack_addresses.netloc("kdc")}
    }}
[domain_realm]
    {host} = {KERBEROS_REALM}
"""


def facts() -> dict:
    """What the browser suite asks of the domain, from the one place that decides it."""

    def relative(dn: str) -> str:
        return dn.removesuffix(f",{BASE_DN}")

    return {
        "realm": KERBEROS_REALM,
        "container": stack_addresses.container("ad"),
        "keycloakHost": keycloak_host(),
        "servicePrincipal": service_principal(),
        "people": relative(PEOPLE),
        "roles": relative(ROLES),
        "departments": relative(DEPARTMENTS),
        "groupPaths": {relative(dn): path for dn, path in GROUP_PATHS.items()},
        "federation": FEDERATION,
        "principalAccount": PRINCIPAL_ACCOUNT,
        "upnSuffix": UPN_SUFFIX,
    }


# == the domain's half =============================================================================


def _in(service: str, *command: str, given: bytes | None = None) -> bytes:
    """Run one command in one of the stack's containers, as the user it runs as."""
    result = subprocess.run(
        ["docker", "exec", "-i", stack_addresses.container(service), *command],
        input=given,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        failed = " ".join(command[:2])
        raise RuntimeError(f"{failed} failed in {service}: {result.stderr.decode().strip()}")
    return result.stdout


def _on_the_domain(*command: str) -> str:
    """Run one command on the domain controller, as its administrator."""
    return _in("ad", *command).decode()


def hand_the_keytab_to_keycloak() -> None:
    """The keytab, from the domain controller to Keycloak — as a file, the way it is handed over.

    Readable by Keycloak alone: it holds the key every ticket for Keycloak is sealed with. Written
    whole and then moved into place, so a sign-on never reads half of it. A recreated Keycloak has
    lost it; `apply` and `keytab` hand it over again.
    """
    _in(
        "keycloak",
        "bash",
        "-c",
        'umask 077 && cat > "$1.new" && mv "$1.new" "$1"',
        "keytab",
        KEYTAB_IN_KEYCLOAK,
        given=_in("ad", "cat", KEYTAB_ON_THE_DOMAIN),
    )


def give_keycloak_a_principal() -> None:
    """What the domain administrator does: an account, its SPN, AES, and the keytab.

    On Windows the same four steps are `New-ADUser`, `setspn -S`, the account's two *supports
    Kerberos AES* boxes, and `ktpass` — `docs/INTEGRATIONS.md` §2, *Windows sign-on*, has them.
    """
    spn = service_principal().split("@")[0]
    users = _on_the_domain("samba-tool", "user", "list").split()
    if PRINCIPAL_ACCOUNT not in users:
        _on_the_domain(
            "samba-tool",
            "user",
            "create",
            PRINCIPAL_ACCOUNT,
            "--random-password",
            f"--userou={SERVICE_ACCOUNTS.removesuffix(f',{BASE_DN}')}",
        )
        _on_the_domain("samba-tool", "user", "setexpiry", PRINCIPAL_ACCOUNT, "--noexpiry")
    _on_the_domain(
        "python3",
        "-c",
        "import ldb, sys\n"
        "from samba.samdb import SamDB\n"
        "from samba.param import LoadParm\n"
        "from samba.auth import system_session\n"
        "lp = LoadParm(); lp.load_default()\n"
        "db = SamDB(url='/var/lib/samba/private/sam.ldb', session_info=system_session(), lp=lp)\n"
        "m = ldb.Message(); m.dn = ldb.Dn(db, sys.argv[1])\n"
        "m['e'] = ldb.MessageElement(sys.argv[2], ldb.FLAG_MOD_REPLACE,"
        " 'msDS-SupportedEncryptionTypes')\n"
        "db.modify(m)\n",
        f"CN={PRINCIPAL_ACCOUNT},{SERVICE_ACCOUNTS}",
        str(AES_ONLY),
    )
    if spn not in _on_the_domain("samba-tool", "spn", "list", PRINCIPAL_ACCOUNT):
        _on_the_domain("samba-tool", "spn", "add", spn, PRINCIPAL_ACCOUNT)
    _on_the_domain("rm", "-f", KEYTAB_ON_THE_DOMAIN)
    _on_the_domain(
        "samba-tool",
        "domain",
        "exportkeytab",
        KEYTAB_ON_THE_DOMAIN,
        f"--principal={service_principal()}",
    )
    hand_the_keytab_to_keycloak()


# == the realm's half ==============================================================================


def _admin_token() -> str:
    """A master-realm token. One per call: they live sixty seconds."""
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": stack_addresses.setting("KEYCLOAK_ADMIN"),
            "password": stack_addresses.setting("KEYCLOAK_ADMIN_PASSWORD"),
        }
    ).encode()
    request = urllib.request.Request(
        f"{stack_addresses.url('keycloak')}/realms/master/protocol/openid-connect/token",
        data=form,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed scheme
        return json.loads(response.read())["access_token"]


def _admin(method: str, path: str, body: object = None) -> tuple[object, str]:
    """One call to the realm's administration API: the parsed answer and `Location`, if any."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{stack_addresses.url('keycloak')}/admin/realms/{REALM}{path}", data=data, method=method
    )
    request.add_header("Authorization", f"Bearer {_admin_token()}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed scheme
        payload = response.read()
        return (json.loads(payload) if payload else None), response.headers.get("Location", "")


def _put_or_post(existing: dict | None, wanted: dict) -> str:
    """Update the component in place where it exists — an edit, as an administrator makes it."""
    if existing:
        _admin("PUT", f"/components/{existing['id']}", {**wanted, "id": existing["id"]})
        return str(existing["id"])
    _, location = _admin("POST", "/components", wanted)
    return location.rstrip("/").rsplit("/", 1)[-1]


def _federation() -> dict | None:
    found, _ = _admin("GET", f"/components?type={STORAGE}&name={urllib.parse.quote(FEDERATION)}")
    return found[0] if found else None  # type: ignore[index]


def _kerberos_step() -> tuple[str, dict]:
    """The browser flow's Kerberos step: the flow's alias and the execution itself."""
    realm, _ = _admin("GET", "")
    alias = realm["browserFlow"]  # type: ignore[index]
    steps, _ = _admin("GET", f"/authentication/flows/{urllib.parse.quote(alias)}/executions")
    for step in steps:  # type: ignore[union-attr]
        if step.get("providerId") == "auth-spnego":
            return alias, step
    raise RuntimeError(f"the browser flow '{alias}' has no Kerberos step")


def _require_kerberos(requirement: str) -> None:
    """Keycloak turns the step on with the federation and off with it; asked, not assumed."""
    alias, step = _kerberos_step()
    if step["requirement"] != requirement:
        _admin(
            "PUT",
            f"/authentication/flows/{urllib.parse.quote(alias)}/executions",
            {**step, "requirement": requirement},
        )


def apply(cache_lifespan: int) -> None:
    give_keycloak_a_principal()
    bind_password = _on_the_domain("cat", BIND_SECRET)
    realm, _ = _admin("GET", "")
    realm_id = str(realm["id"])  # type: ignore[index]
    existing = _federation()
    wanted = federation(realm_id, bind_password, cache_lifespan)
    parent = _put_or_post(existing, wanted)
    if not existing:
        # Keycloak fills the vendor's defaults in when a federation is created — `userPrincipalName`
        # as the Kerberos principal attribute among them — and only an edit empties a field again.
        _admin("PUT", f"/components/{parent}", {**wanted, "id": parent})
    mappers, _ = _admin("GET", f"/components?parent={parent}&type={MAPPER}")
    by_name = {m["name"]: m for m in mappers}  # type: ignore[union-attr]
    for groups_dn, path in GROUP_PATHS.items():
        wanted = group_mapper(parent, groups_dn, path)
        _put_or_post(by_name.get(wanted["name"]), wanted)
    # Found by what it does rather than by name: an installation may have renamed it.
    controls = [m for m in mappers if m["providerId"] == ACCOUNT_CONTROLS]  # type: ignore[union-attr]
    _put_or_post(controls[0] if controls else None, account_controls(parent))
    _require_kerberos("ALTERNATIVE")
    print(f"Windows sign-on is on for realm '{REALM}':")
    print(f"  service principal  {service_principal()}  (its keytab handed to Keycloak)")
    print(f"  directory          {PEOPLE}, read-only")
    for groups_dn, path in GROUP_PATHS.items():
        print(f"  groups             {groups_dn} -> {path}")
    print(f"  a change in AD reaches AIRA within {cache_lifespan}s, plus one token lifetime")


def remove() -> None:
    """Take the realm's half away again: the federation, its people, and the Kerberos step.

    Deleting the federation removes the people it imported, and Keycloak turns the browser flow's
    Kerberos step back off with it — which is checked rather than trusted. The domain keeps its
    principal: it is the domain's, and it opens nothing without a realm that accepts it.
    """
    existing = _federation()
    if existing:
        _admin("DELETE", f"/components/{existing['id']}")
    _require_kerberos("DISABLED")
    print(f"Windows sign-on is off for realm '{REALM}'.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    on = commands.add_parser("apply", help="give the domain and the realm their halves")
    on.add_argument("--cache-lifespan", type=int, default=CACHE_LIFESPAN_SECONDS, metavar="SECONDS")
    commands.add_parser("remove", help="take the realm's half away again")
    commands.add_parser("keytab", help="export Keycloak's key and hand it over again")
    commands.add_parser("facts", help="what the browser suite needs to know, as JSON")
    commands.add_parser("workstation", help="a krb5.conf for a browser standing in for a PC")
    arguments = parser.parse_args(argv)

    if arguments.command == "facts":
        print(json.dumps(facts()))
        return 0
    if arguments.command == "workstation":
        print(workstation_krb5_conf(), end="")
        return 0
    if arguments.command == "keytab":
        give_keycloak_a_principal()
        return 0

    environment = stack_addresses.setting("AIRA_ENVIRONMENT")
    if environment not in DEMO_ENVIRONMENTS:
        print(f"environment '{environment}' — this realm is somebody else's to configure")
        return 1
    try:
        if arguments.command == "apply":
            apply(arguments.cache_lifespan)
        else:
            remove()
    except urllib.error.HTTPError as error:
        print(f"Keycloak refused {error.url}: {error.code} {error.read().decode()[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
