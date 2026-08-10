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
    assert services["vault-init"].get("profiles") == ["demo"], (
        "a real Vault is provisioned by whoever owns it; this must not run against one"
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
