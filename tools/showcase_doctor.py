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
        # A suffixed username is the signature of a subject that changed underneath a binding.
        suffixed = "-" in username and len(username.rsplit("-", 1)[-1]) == 8
        say(not suffixed, f"user {line}")
        if suffixed:
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


def main() -> int:
    print("Checking the showcase, link by link. Nothing here changes anything.")
    check_identity()
    check_management()
    check_gateway()

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
