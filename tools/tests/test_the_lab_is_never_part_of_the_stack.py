"""The laboratory overlay stays out of the stack the documentation describes.

`docker-compose.lab.yml` exists so that *"can this reach a machine on my network"* and *"what
would that machine receive"* can be answered without editing the three files everything else
reads. That is only worth anything while it stays a **fourth** file: the moment it is added to
`CORE` or `SHOWCASE`, every deployment and every walkthrough is running somebody's experiment, and
`make up` stops meaning what `docs/deployment/` says it means.

`tools/compose_files.py` opens with what a mishandled split costs — *"sixteen places named the
files by hand … the ones that were missed would not fail loudly"*. This is that rule pointed at
the file most likely to drift into the list, because adding it is a one-line convenience every
time somebody wants their experiment to "just be there".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import compose_files  # noqa: E402

LAB_FILE = compose_files.COMPOSE_DIR / "docker-compose.lab.yml"


def test_the_overlay_exists_where_the_registry_says() -> None:
    """A guard on the guard: every assertion below passes vacuously against a missing file."""
    assert compose_files.LAB == (LAB_FILE,)
    assert LAB_FILE.exists()


@pytest.mark.parametrize("name", ["CORE", "SHOWCASE", "ALL"])
def test_the_overlay_is_in_none_of_the_stack_lists(name: str) -> None:
    assert LAB_FILE not in getattr(compose_files, name), (
        f"{LAB_FILE.name} is in {name}, so every caller of that list now runs the laboratory. "
        "It is a fourth file on purpose — `make up-lab` adds it and nothing else does."
    )


def test_only_the_lab_target_starts_compose_with_the_overlay() -> None:
    """The Makefile is the other place the split can be undone, and it undoes it silently.

    The property is narrow on purpose: not *"which lines mention `LAB_F`"* — the `up-lab` recipe
    prints the variable's name so a reader knows what to drop, and a first draft of this test
    failed on its own help text. What must stay true is that no **Compose invocation** other than
    `up-lab`'s carries the overlay, because that is the only way `make up` can quietly start
    running somebody's experiment.
    """
    target, invocations = None, {}
    for line in (ROOT / "Makefile").read_text().splitlines():
        if line and not line[0].isspace() and ":" in line and not line.startswith("\t"):
            target = line.split(":", 1)[0].strip()
        stripped = line.lstrip("\t").lstrip()
        if stripped.startswith("#") or stripped.startswith("@#"):
            continue
        if "LAB_F" in line and ("docker compose" in line or line.startswith("COMPOSE")):
            invocations.setdefault(target, []).append(line.strip())

    assert list(invocations) == ["up-lab"], (
        f"the overlay reaches Compose from {sorted(invocations)}. It belongs to `up-lab` alone — "
        "any other target that gains it changes what that target starts, and no document says so."
    )


def test_the_overlay_variable_is_defined_once() -> None:
    definitions = [
        line for line in (ROOT / "Makefile").read_text().splitlines() if line.startswith("LAB_F")
    ]
    assert len(definitions) == 1, f"LAB_F is defined {len(definitions)} times: {definitions}"


def test_the_overlay_adds_no_service_of_its_own() -> None:
    """It **configures** the collector; it does not introduce components.

    A service defined only here would exist for `make up-lab` and for nothing else — invisible to
    `test_compose_images_are_buildable`, to the lifecycle check, and to every reader of the three
    files. An overlay that adds a component is a fork of the stack wearing an overlay's name.
    """
    text = LAB_FILE.read_text()
    services = [
        line.rstrip(":").strip()
        for line in text.splitlines()
        if line.startswith("  ") and line.rstrip().endswith(":") and not line.startswith("    ")
    ]
    known = compose_files.INFRA.read_text() + compose_files.APPS.read_text()
    unknown = [name for name in services if f"\n  {name}:" not in known]

    assert not unknown, (
        f"{unknown} exist only in the overlay. Add them to the stack files if they are real, or "
        "keep the overlay to configuring what is already there."
    )


def test_the_endpoint_has_no_default() -> None:
    """An exporter with nowhere to send does not fail — it **retries**, with growing backoff, while
    holding telemetry in memory. Measured at 24 s, 36 s and 44 s against a dead endpoint, with the
    only symptom a line in a log nobody was reading. Compose's `:?` turns that into a refusal that
    names the variable."""
    assert "${LAB_SIEM_ENDPOINT:?" in LAB_FILE.read_text(), (
        "LAB_SIEM_ENDPOINT must be required (`${LAB_SIEM_ENDPOINT:?…}`), not defaulted: a default "
        "would start a collector that retries into the void and says so only in its own log."
    )


def test_the_laboratory_knobs_stay_out_of_the_product_contract() -> None:
    """`LAB_*`, never `AIRA_*`.

    `AIRA_*` is the settings contract — rendered from a config file, listed in
    `docs/CONFIGURATION.md`, checked against the settings classes in both directions. A laboratory
    knob in that namespace would be a setting the product does not have, arriving in the one place
    a reader trusts to be complete.
    """
    text = LAB_FILE.read_text()
    strays = sorted({word for word in text.split() if word.startswith("AIRA_LAB")})
    assert not strays, f"{strays} put a laboratory knob into the product's settings namespace"
