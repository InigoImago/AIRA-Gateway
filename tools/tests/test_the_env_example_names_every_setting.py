"""The file you copy tells you every knob there is — or says why one is missing.

`deploy/compose/.env.example` is what `make env` copies to `.env`, so it is the first and for many
installations the *only* place an operator meets this product's configuration. It named **15 of
90 settings**: the ports, Kafka's security, Vault, and a handful of upstreams. The other 75 were
all settable — `docker-compose.apps.yml` passes 84 of them as `${VAR:-default}` — and there was
nothing in the file to say so, so the only way to find one was to read the settings classes.

That is the shape `LESSONS.md` §1 calls *a named bound that nothing reads*, inverted: a knob that
exists, works, is passed all the way to the container, and appears in no document the operator
opens. `docs/CONFIGURATION.md` is the full reference and is excellent, and it is not the file
`make env` copies.

**Not a demand that every line be uncommented.** An uncommented line *is* the value, and a file
that sets ninety of them is one where nobody can see which six were decisions — so the check is
that the name is *present*, commented or not.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"

#: Settings a deployment deliberately cannot set, and the reason. Each is a claim somebody has to
#: defend, which is why it carries prose rather than sitting in a bare set.
NOT_FOR_A_DEPLOYMENT = {
    "AIRA_TEST_DATABASE": (
        "an in-memory SQLite, set by the test harness. A deployment that turned it on would come "
        "up with an empty database and lose it on restart."
    ),
    "AIRA_GIT_COMMIT": "build provenance, stamped into the image at build time",
    "AIRA_GIT_BRANCH": "build provenance, stamped into the image at build time",
    "AIRA_BUILD_NUMBER": "build provenance, stamped into the image at build time",
    "AIRA_BUILD_TIME": "build provenance, stamped into the image at build time",
}


def _settings_names() -> set[str]:
    """Every `AIRA_*` a settings class would accept, from both planes."""
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    names: set[str] = set()
    for cls in (GatewaySettings, ManagementSettings):
        names |= {f"AIRA_{field.upper()}" for field in cls.model_fields}
    return names


def _named_in_the_example() -> set[str]:
    """Names the file offers as settable — `AIRA_X=…`, commented out or not.

    Deliberately not "appears anywhere in the file": a name inside a paragraph explaining a
    *different* setting would satisfy that, and the operator scanning for an assignment would
    still not find one.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*#?\s*(AIRA_[A-Z0-9_]+)\s*=", text, re.M))


def test_the_example_is_readable() -> None:
    """A guard on the guard: a path typo would make everything below vacuous."""
    assert len(ENV_EXAMPLE.read_text(encoding="utf-8")) > 5_000


def test_every_setting_is_named_or_deliberately_absent() -> None:
    missing = sorted(_settings_names() - _named_in_the_example() - set(NOT_FOR_A_DEPLOYMENT))

    assert not missing, (
        f"These settings exist, reach the container, and appear nowhere in the file `make env` "
        f"copies: {missing}. Add each as a commented line with what it does — or, if a deployment "
        "should not set it, to NOT_FOR_A_DEPLOYMENT with the reason."
    )


def test_the_deliberate_absences_are_still_settings() -> None:
    """A waiver that outlives its setting silently covers the next one to take the name."""
    stale = sorted(set(NOT_FOR_A_DEPLOYMENT) - _settings_names())
    assert not stale, f"These are waived and are settings nowhere: {stale}."


def test_a_waived_setting_is_not_offered_anyway() -> None:
    """Both halves have to agree, or the file offers a knob the guard says nobody may turn."""
    offered = sorted(set(NOT_FOR_A_DEPLOYMENT) & _named_in_the_example())
    assert not offered, (
        f"These are waived as not-for-a-deployment and the file offers them as settable: "
        f"{offered}. One of the two is wrong."
    )


def test_the_file_says_which_stack_each_recipe_starts() -> None:
    """The question this file is opened with is *what do I run to get X* (the operator's words,
    2026-09-02). Every `make` target that starts something has to appear, or the file answers a
    question nobody asked before the one they did."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for target in ("make up", "make up-core", "make up-apps", "make up-full", "make showcase"):
        assert target in text, f"`{target}` starts a stack and this file does not mention it"


def _compose_only_names() -> set[str]:
    """Names Compose itself reads — ports, bind addresses, the stack prefix.

    Borrowed from the guard next door rather than listed again: two copies of *which names are
    not settings* is two answers, and this file would be the one that goes stale.
    """
    from tools.tests.test_compose_passes_the_settings_it_names import COMPOSE_ONLY

    return set(COMPOSE_ONLY)


def test_no_example_line_names_a_variable_that_nothing_defines() -> None:
    """A knob wired to a name that does not exist reads as done and is worse than an absent one.

    `test_one_owner_for_the_stack_addresses.py` records `AIRA_KEYCLOAK_PORT` as exactly that
    defect in the Makefile — one occurrence in the whole repository, nothing setting it, so the
    report always went to `8080`. The copy in **this** file outlived that fix: it sat in the
    two-stacks example alongside `AIRA_POSTGRES_PORT`, which is the *setting* the paragraph three
    lines below warns against reusing for a published port. Both were wrong, in the file an
    operator copies, next to the prose explaining why.
    """
    named = _named_in_the_example()
    phantom = sorted(named - _settings_names() - _compose_only_names())

    assert not phantom, (
        f"These are offered as settable in `.env.example` and nothing reads them: {phantom}. "
        "Either the name is a typo, or it was renamed and this file was not — both read to an "
        "operator as a knob that works."
    )


def test_no_key_is_defined_twice() -> None:
    """A key set twice is a file whose meaning depends on line order.

    Docker Compose takes the **last** definition and says nothing about the first, so a value
    somebody appended silently beats the one they edited — and the edited one is the one they will
    go back and read. Found in a live `deploy/compose/.env` on 2026-09-02: `AIRA_BIND_HOST` at line
    10 (`127.0.0.1`, from this example) and again at line 123 (`0.0.0.0`, appended later). The
    stack was reachable, the file said loopback at the place anybody looks, and rebuilding `.env`
    from the example silently took the override away.

    Checked on the example rather than on `.env`, because `.env` is git-ignored and belongs to the
    operator; what this repository can promise is that the file it hands out does not teach the
    shape. `make config-verify` is the check for a deployment's own file.
    """
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for number, line in enumerate(ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in seen:
            duplicates.append(f"{key} (lines {seen[key]} and {number})")
        seen[key] = number

    assert not duplicates, (
        f"These keys are defined more than once: {duplicates}. Compose takes the last and says "
        "nothing about the first, so the file means something other than what it reads like."
    )
