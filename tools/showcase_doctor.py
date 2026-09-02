"""Say which link of the demo is broken, instead of leaving somebody to guess.

The showcase is a chain: Keycloak holds the accounts and the groups, Management holds the use
cases and works out roles from those groups, Kafka carries the configuration to the gateway, and
the gateway holds a read-model it serves from. Every link has now been seen broken in one week —
a realm that predated the file, a Vault that had forgotten its path, a catalog written and never
announced, budgets spent by an earlier run. Each time the symptom was the same shape: **a console
that comes up and shows nothing**, with nothing anywhere saying why.

So this reports the chain, link by link, and names the first thing that is wrong along with what
to do about it. It reads; it changes nothing.

Deliberately not part of `make showcase`: a demo that runs a diagnostic every time is a demo that
has given up on working. This is what you run when it did not.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import stack_addresses

KEYCLOAK = os.environ.get("KEYCLOAK_URL") or stack_addresses.url("keycloak")
ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")
REALM = os.environ.get("KEYCLOAK_REALM", "aira")
COMPOSE = ["docker", "compose", "-f", "deploy/compose/docker-compose.yml"]
STACK = os.environ.get("AIRA_STACK", "aira")

#: What the showcase seeds. Named here rather than imported, because this has to run when the
#: Python environment is the *one thing* that is fine and everything else is not.
DEMO_USE_CASES = ("kundenservice", "entwicklung", "personalwesen", "coding-assistant")
DEMO_ACCOUNTS = ("admin", "itsec", "itgov", "ucadmin", "ucuser")

problems: list[str] = []


def say(ok: bool | None, what: str, detail: str = "") -> None:
    mark = {True: "ok  ", False: "BAD ", None: "??  "}[ok]
    print(f"  {mark} {what}{(' — ' + detail) if detail else ''}")


def blame(problem: str, remedy: str) -> None:
    problems.append(f"{problem}\n       → {remedy}")


def psql(database: str, sql: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "aira", "-d", database, "-tAc", sql],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr else "psql")
    return [line for line in result.stdout.splitlines() if line]


def keycloak(path: str, token: str) -> object:
    request = urllib.request.Request(f"{KEYCLOAK}{path}")
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed scheme
        return json.loads(response.read())


def token() -> str:
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
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed scheme
        return json.loads(response.read())["access_token"]


def check_identity() -> None:
    print("\nKeycloak — who can sign in, and what groups say about them")
    try:
        admin_token = token()
    except Exception as error:  # noqa: BLE001
        say(False, "admin API", str(error))
        blame(
            "Keycloak is not answering as an administrator.",
            "Is the stack up? `make up-full`. The credentials are KEYCLOAK_ADMIN(_PASSWORD).",
        )
        return

    try:
        users = keycloak(f"/admin/realms/{REALM}/users?max=200", admin_token)
    except urllib.error.HTTPError as error:
        say(False, f"realm '{REALM}'", "not found" if error.code == 404 else str(error))
        blame(
            f"There is no realm '{REALM}'.",
            "`make up-full` — the keycloak-init service imports it from the file.",
        )
        return

    names = {u["username"] for u in users}  # type: ignore[index]
    missing = [name for name in DEMO_ACCOUNTS if name not in names]
    say(
        not missing, f"realm '{REALM}' accounts", f"{len(names)} present, missing: {missing or '—'}"
    )
    if missing:
        blame(
            f"The realm is missing {missing}.",
            "`docker compose ... up -d --force-recreate keycloak-init` re-imports it "
            "from the file.",
        )

    mapping = os.environ.get("AIRA_ROLE_GROUPS", "")
    say(
        bool(mapping) or None,
        "AIRA_ROLE_GROUPS in this shell",
        mapping or "unset here (the containers have their own default)",
    )


#: What Management appends when a directory subject changes underneath a binding: `f"{preferred}-
#: {subject[:8]}"` (`apps/api/authentication.py`), so the suffix is the first eight characters of a
#: **UUID** — hexadecimal, and nothing else.
#:
#: **It was `len(...) == 8`, and that is any eight-letter word.** The shipped realm creates
#: `service-account-aira-integration-tests-security`, whose last segment is `security`: eight
#: characters, entirely legitimate, and reported as a duplicate on every run of this tool against a
#: healthy stack — `make showcase-doctor` exited 1 and printed *"1 thing(s) to fix"* about an
#: account this repository itself ships.
#:
#: That is the failure `LESSONS.md` §3 names: **a check that cries wolf on the supported path is
#: one nobody reads on the day it is right.** A doctor whose one finding is always wrong teaches
#: its reader to skip the section, which is the section that would have named a real duplicate.
_DUPLICATE_SUFFIX = re.compile(r"^[0-9a-f]{8}$")


def is_duplicated_username(username: str) -> bool:
    """Whether this name is the one Management invents for a second account on a changed subject."""
    return "-" in username and bool(_DUPLICATE_SUFFIX.match(username.rsplit("-", 1)[-1]))


def check_management() -> None:
    print("\nManagement — the use cases the console lists, and who may see them")
    try:
        rows = psql("aira_mgmt", "select slug from usecases_usecase")
    except Exception as error:  # noqa: BLE001
        say(False, "database", str(error))
        blame("Management's database is unreachable.", "Is the stack up? `make up-full`.")
        return

    present = set(rows)
    missing = [slug for slug in DEMO_USE_CASES if slug not in present]
    say(not missing, "demo use cases", f"{len(present)} total, missing: {missing or '—'}")
    if missing:
        blame(
            f"The seed has not created {missing}.",
            "`docker compose ... --profile demo run --rm management-seed`, then `make showcase`.",
        )

    groups = psql(
        "aira_mgmt",
        "select u.username || ' -> ' || coalesce(string_agg(g.name, ','), '(none)') "
        "from auth_user u left join auth_user_groups ug on ug.user_id = u.id "
        "left join auth_group g on g.id = ug.group_id group by u.username order by u.username",
    )
    for line in groups:
        username = line.split(" -> ")[0]
        say(not is_duplicated_username(username), f"user {line}")
        if is_duplicated_username(username):
            blame(
                f"'{username}' is a duplicate created after a Keycloak subject changed.",
                "See deploy/compose/README.md — rebind api_oidcidentity.subject, do not delete the "
                "original, whose API keys cascade with it.",
            )
    if not any(line.startswith("admin ") for line in groups):
        blame(
            "No demo account has ever signed in to Management.",
            "Roles and visibility are worked out at sign-in from the token's groups; open the "
            f"console at {stack_addresses.url('console')} once.",
        )


def check_gateway() -> None:
    print("\nGateway — the read-model it actually serves from")
    try:
        use_cases = psql("aira_gateway", "select count(*) from use_cases")[0]
        models = psql("aira_gateway", "select count(*) from model_catalog")[0]
    except Exception as error:  # noqa: BLE001
        say(False, "database", str(error))
        blame("The gateway's database is unreachable.", "Is the stack up? `make up-full`.")
        return

    say(int(use_cases) > 0, "use cases received over Kafka", use_cases)
    say(int(models) > 0, "models received over Kafka", models)

    # The demo's keys, by name. A key belonging to a use case that was once deleted stays revoked
    # **for ever** — deliberately, so that no `api_key.created` can resurrect one (`ADR-0007`) —
    # and because the demo's keys are deterministic, re-running the seed re-announces the same
    # prefix and changes nothing. The result is every request answered `401` with a stack that
    # otherwise looks perfect, which is why this is checked by name rather than counted.
    keys = psql(
        "aira_gateway",
        "select use_case || ' ' || case when is_active then 'active' else 'REVOKED' end "
        f"from api_keys where use_case in ({', '.join(repr(s) for s in DEMO_USE_CASES)})",
    )
    state = dict(line.split(" ") for line in keys)
    missing_keys = [slug for slug in DEMO_USE_CASES if slug not in state]
    revoked = [slug for slug, value in state.items() if value == "REVOKED"]
    say(
        not missing_keys and not revoked,
        "demo API keys",
        f"{len(state)} known, revoked: {revoked or '—'}",
    )
    if missing_keys:
        blame(
            f"The gateway has no key for {missing_keys}, so those requests are refused with 401.",
            "The seed announces them over Kafka; check `docker logs aira-management-relay` and "
            "`docker logs aira-gateway-consumer`, then re-run the seed.",
        )
    if revoked:
        blame(
            f"The demo keys for {revoked} are revoked, and revocation is terminal by design — "
            "re-running the seed re-announces the same prefix and cannot bring them back.",
            "`make showcase-reset-keys` — demo only, and deliberately its own command: deleting "
            "rows from the read-model that authorization is drawn from is not a habit to encode "
            "into a target that runs every time.",
        )
    if int(models) == 0:
        blame(
            "The gateway knows no models, so every request is refused as 'not in the model "
            "catalog'.",
            "The seed announces them; re-run it and give the relay a moment: "
            "`docker compose ... --profile demo run --rm management-seed`.",
        )
    if int(use_cases) == 0:
        blame(
            "The gateway has no use cases, so every API key is refused.",
            "Same cause as above — the events are not arriving. Check `docker logs "
            "aira-management-relay` and `docker logs aira-gateway-consumer`.",
        )


def check_the_browsers_login_chain() -> None:
    """The half of the chain this file did not walk: what the **browser** is told to reach.

    Every other check here runs from inside the network and asks *can this container reach that
    one*. The login does not work that way. `runtime-config.js` hands the browser an issuer, the
    console's content policy names one origin, and Keycloak compares the redirect against a pinned
    list — three addresses that are resolved **on the reader's machine**, not on this one.

    So a stack can be green on every line above and still refuse every login, and the report a
    person brings is `keycloak not reachable`. That happened; this is the counterpart.

    Deliberately reports rather than judges the last part: this program cannot know where the
    browser is, and a check that assumed `localhost` would call a correctly-configured remote
    deployment broken. What it can do is print the three addresses together and say what they have
    to be true *of*, which is the sentence nobody had.
    """
    print("\nThe login, as the browser sees it")
    console = stack_addresses.url("console")

    try:
        config = _get_text(f"{console}/runtime-config.js")
    except OSError as error:
        say(False, "runtime-config.js", str(error))
        blame(
            "The console does not serve its runtime configuration, so the SPA has no issuer at "
            "all and cannot start a login.",
            f"Check that the frontend container is up: docker logs {STACK}-frontend",
        )
        return

    issuer = _between(config, "issuer: '", "'")
    say(bool(issuer), "issuer handed to the browser", issuer or "none found in runtime-config.js")

    policy = _header(console, "content-security-policy")
    connect = _directive(policy, "connect-src")
    origin = _origin(issuer)
    allowed = bool(origin) and origin in connect
    say(allowed, "content policy allows that origin", connect or "no connect-src")
    if issuer and not allowed:
        blame(
            f"The console may not call {origin}: its content policy allows '{connect}'. The token "
            "request is blocked by the browser, and by nothing that leaves a trace on this side.",
            "Set AIRA_CSP_CONNECT_SRC together with AIRA_OIDC_ISSUER — they are one decision.",
        )

    # Reachable **from here**, which is a different machine from the browser's. Said as such.
    if issuer:
        try:
            _get_text(f"{issuer}/.well-known/openid-configuration")
            say(True, "that issuer answers from this machine", issuer)
        except OSError as error:
            say(False, "that issuer answers from this machine", str(error))
            blame(
                f"Nothing answers at {issuer}, so no browser can either.",
                "Check the Keycloak container and AIRA_OIDC_ISSUER.",
            )

    redirects = _redirect_uris()
    say(bool(redirects), "redirect URIs the realm accepts", ", ".join(redirects) or "none")
    console_allowed = any(uri.rstrip("/*").rstrip("/") == console for uri in redirects)
    say(console_allowed, f"…includes {console}", "" if console_allowed else "it does not")

    # **The sentence the whole check exists for.** Everything above is true of *this* machine, and
    # the browser is somewhere else — which is the one fact none of the green marks carries.
    print(
        "\n  These three addresses are resolved by the BROWSER, not by this machine. If you open"
        f"\n  the console from anywhere but here, {origin or 'the issuer'} and the redirect URIs"
        "\n  above must be reachable and correct FROM THERE — see the section 'REACHING THIS"
        "\n  STACK FROM ANOTHER MACHINE' in deploy/compose/.env.example."
    )


def _get_text(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def _header(url: str, name: str) -> str:
    try:
        request = urllib.request.Request(url, method="HEAD")  # noqa: S310
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return str(response.headers.get(name, ""))
    except OSError:
        return ""


def _between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    rest = text.split(start, 1)[1]
    return rest.split(end, 1)[0] if end in rest else ""


def _directive(policy: str, name: str) -> str:
    for part in policy.split(";"):
        if part.strip().startswith(name):
            return part.strip()
    return ""


def _origin(url: str) -> str:
    """`scheme://host:port` of ``url``, which is what a content policy names."""
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def _redirect_uris() -> list[str]:
    """What the realm's console client will actually redirect to. Empty if it cannot be asked."""
    try:
        # `keycloak()` and `token()` are this file's own, so the admin exchange is written once.
        clients = keycloak(f"/admin/realms/{REALM}/clients?clientId=aira-gateway", token())
        if isinstance(clients, list) and clients:
            return [str(uri) for uri in clients[0].get("redirectUris", [])]
        return []
    except OSError, ValueError, IndexError, KeyError, AttributeError:
        return []


def main() -> int:
    print("Checking the showcase, link by link. Nothing here changes anything.")
    check_identity()
    check_management()
    check_gateway()
    check_the_browsers_login_chain()

    print()
    if not problems:
        print("Everything the demo needs is in place.")
        print("If the console still looks empty, sign out and in again — visibility is worked out")
        print("from the token's groups at sign-in.")
        return 0

    print(f"{len(problems)} thing(s) to fix, in this order:\n")
    for index, problem in enumerate(problems, start=1):
        print(f"  {index}. {problem}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
