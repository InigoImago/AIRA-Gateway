"""The showcase must tell the same story every time it is run.

Two properties, both broken and both found by running the target rather than by reading it.

**Vault.** The stack's Vault runs `server -dev`, which keeps everything in memory. Recreate the
container — which `docker compose down` does — and `secret/aira` is gone; `load_secrets()` then
fails closed, correctly, and every application container refuses to boot. So `make showcase`
silently required somebody to have run `make vault-init` *after* the current Vault container
started: follow the documentation, then bring the stack down, and the one command that must always
work stops working. The provisioning is a service with a condition on it now.

**Consumption.** Budgets are calibrated so a handful of requests moves each bar into the middle of
its range. The second run of a day therefore found them spent and answered 429 to six of ten
requests — including the prompt-injection case, whose whole point is to be refused by the
*pipeline*. Still true, and about yesterday.

These are checked against the files rather than by running Docker, for the reason `FRD-130`'s own
tests give: what must not regress is the *wiring*, and the wiring is readable. A run is what found
them; a test is what keeps them.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
APPS = ROOT / "deploy" / "compose" / "docker-compose.apps.yml"
MAKEFILE = ROOT / "Makefile"


def _services() -> dict[str, dict]:
    return yaml.safe_load(APPS.read_text())["services"]


def test_the_dev_vault_is_provisioned_by_the_stack_that_wipes_it() -> None:
    services = _services()

    assert "vault-init" in services, (
        "nothing provisions the dev Vault, which forgets on every restart — the stack boots only "
        "for somebody who remembered a separate command"
    )
    # Guarded by the environment rather than by a profile, and that distinction is the second
    # defect this file records: a service that other services depend on cannot itself sit behind a
    # profile, or the project is invalid whenever that profile is off (see the test below). So it
    # is always defined and decides for itself whether there is anything to do.
    command = " ".join(services["vault-init"]["command"])

    assert "profiles" not in services["vault-init"]
    assert "VAULT_ADDR" in command, "it writes even when no Vault is configured"
    assert "AIRA_ENVIRONMENT" in command, (
        "a real Vault is provisioned by whoever owns it, and nothing here says so"
    )


def test_no_service_depends_on_one_a_profile_could_switch_off() -> None:
    """**The project must be valid for every profile combination anyone uses.**

    Compose refuses a project outright — not the one service, the whole project — when a service
    depends on one that the active profiles leave out. `vault-init` was written with
    `profiles: ["demo"]` and two non-profiled migration jobs depending on it, so
    `docker compose -f … -f …` with no profile answered `invalid compose project`. That took CI's
    log-dumping fallback with it, which runs without profiles and exists for the moment something
    else has already gone wrong.

    The rule, stated as the containment it is: whatever enables a service must also enable
    everything it depends on.
    """
    services = _services()

    broken: list[str] = []
    for name, definition in services.items():
        mine = set(definition.get("profiles") or [])
        for dependency in definition.get("depends_on") or {}:
            theirs = set(services.get(dependency, {}).get("profiles") or [])
            # A dependency with no profile is always present. Otherwise it must be enabled by
            # every profile that enables the dependent — and a dependent with no profile is
            # enabled by all of them, so any profile on the dependency is a hole.
            if theirs and (not mine or not theirs <= mine):
                broken.append(f"{name} -> {dependency}")

    assert not broken, (
        f"{broken}: compose rejects the entire project when the dependency's profile is off"
    )


def test_everything_that_reads_a_secret_waits_for_the_vault_to_be_provisioned() -> None:
    """The migrations are the first thing to read a setting, so they are the ones that must wait.
    A service that reads secrets and does not wait is one that fails on a cold stack and passes on
    a warm one — which is precisely how this went unnoticed."""
    services = _services()
    waiting = {
        name
        for name, definition in services.items()
        if "vault-init" in (definition.get("depends_on") or {})
    }

    assert {"gateway-migrate", "management-migrate"} <= waiting, (
        f"only {sorted(waiting)} wait for the Vault to be provisioned"
    )


def test_the_showcase_clears_consumption_before_it_drives_traffic() -> None:
    """Order matters: resetting *after* the traffic would clear the figures the walkthrough is
    about, which is the opposite defect and just as quiet."""
    body = MAKEFILE.read_text()
    showcase = body[body.index("\nshowcase:") : body.index("\nshowcase-traffic:")]

    reset = showcase.index("demo_reset_usage.py")
    traffic = showcase.index("demo_traffic.py")

    assert reset < traffic, "the showcase clears the counters after driving traffic through them"


def test_the_traffic_target_does_not_reset_anything() -> None:
    """`showcase-traffic` exists to fill the bars and reach a limit. Resetting there would remove
    the only way to see enforcement happen."""
    body = MAKEFILE.read_text()
    start = body.index("\nshowcase-traffic:")
    target = body[start : body.index("\n\n", start)]

    assert "demo_reset_usage.py" not in target


def test_the_reset_names_the_use_cases_it_may_clear() -> None:
    """Never "all". A stack that also carries real traffic must not have somebody else's budget
    quietly forgiven by a demo helper."""
    source = (ROOT / "tools" / "demo_reset_usage.py").read_text()

    assert "DEMO_SLUGS" in source
    assert "DELETE FROM budget_usage WHERE " in source
    assert "ANY(:slugs)" in source, "the delete is not bounded by the named use cases"


def test_the_showcase_waits_for_everything_it_then_points_at() -> None:
    """It printed `SPA http://localhost:4200` after waiting for the **gateway** alone.

    On a machine where the frontend needed a few seconds longer, the one URL the whole walkthrough
    starts at answered nothing — and the target had already declared itself finished. Two ideas of
    "ready" in one repository, and this used the weaker one; `wait-healthy` checks the console, both
    APIs and Keycloak, and is what CI uses.
    """
    body = MAKEFILE.read_text()
    showcase = body[body.index("\nshowcase:") : body.index("\nshowcase-traffic:")]

    assert "wait-healthy" in showcase, (
        "the showcase waits for something narrower than what it goes on to advertise"
    )
    assert "curl -fsS http://localhost:8001/readyz" not in showcase, (
        "a second, weaker readiness loop is back"
    )


def test_wait_healthy_covers_the_console_and_not_only_the_apis() -> None:
    """The console is the only one of the four with a user interface, so it is the one whose
    absence a reader notices — and the easiest to leave out of a check written from the API side."""
    body = MAKEFILE.read_text()
    target = body[body.index("\nwait-healthy:") : body.index("\ntest-e2e:")]

    for port in ("4200", "8001", "8002", "8080"):
        assert port in target, f"nothing waits for the service on {port}"


def test_the_printed_walkthrough_names_the_accounts_the_seed_creates() -> None:
    """The login table is the first thing anybody reads, and it goes stale silently.

    It still said `ucadmin` administers "two of the three use cases" after a fourth was added, and
    that `ucuser` is a member of `kundenservice` when it is now also in `coding-assistant`. Nothing
    fails when that drifts — a reader simply finds the console disagreeing with the instructions
    and trusts the instructions, which is the worse of the two.
    """
    from aira_management.apps.seed.contributions.showcase import MEMBERSHIPS, _use_cases

    body = MAKEFILE.read_text()
    block = body[body.index("\nshowcase:") : body.index("\nshowcase-traffic:")]

    seeded = {slug for slug, _ in ((s, None) for s in (u["slug"] for u in _use_cases()))}
    for username in {name for members in MEMBERSHIPS.values() for name, _ in members}:
        assert username in block, f"the seed creates {username} and the walkthrough never says so"

    # Whatever use cases the table names for `ucuser`, it must actually be in — and it must not
    # leave one out. Both directions, because the drift went the second way.
    named = {slug for slug in seeded if f"'{slug}'" in block}
    belongs = {
        slug for slug, members in MEMBERSHIPS.items() if any(n == "ucuser" for n, _ in members)
    }
    invisible = {
        slug for slug, members in MEMBERSHIPS.items() if not any(n == "ucadmin" for n, _ in members)
    }

    assert belongs <= named, f"{sorted(belongs - named)}: ucuser is in it and the table omits it"
    assert invisible <= named, f"{sorted(invisible - named)}: invisible to ucadmin and unmentioned"
