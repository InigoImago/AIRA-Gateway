"""No package is locked below the first release its published advisories leave alone.

`pip-audit` asks the network, and its answer changes whenever an advisory is published, so it is
not a gate a commit can be held to. What a commit **can** be held to is what this repository has
already learned: each entry below is a package the workspace installs, the first release no
advisory names, releases that are named, and why it matters here. Two properties, because either
alone lets the old release back in:

- **the lock holds the package at or above its floor** — every image and every CI job installs
  exactly the lock (`uv sync --frozen`), so the lock is what runs;
- **the workspace declares the floor** — in a member's dependencies, or for a package the workspace
  reaches only through another one as a root `constraint-dependencies` entry — so the next re-lock
  cannot resolve back below it.

A new advisory is a new entry here, beside the upgrade that answers it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "uv.lock"
#: The workspace root and its members — the files a re-lock resolves from.
PYPROJECTS = (
    ROOT / "pyproject.toml",
    ROOT / "libs" / "pyproject.toml",
    ROOT / "gateway" / "pyproject.toml",
    ROOT / "management" / "backend" / "pyproject.toml",
)


@dataclass(frozen=True, slots=True)
class Floor:
    #: The first release no published advisory names.
    minimum: str
    #: Releases an advisory names, which no declaration may admit — the ones this repository ran.
    affected: tuple[str, ...]
    #: What the advisory means for this workspace.
    why: str


FLOORS = {
    "opentelemetry-api": Floor(
        "1.45.1",
        ("1.44.0",),
        "GHSA-xcq7-p3pf-p88h: `tracestate` parsed in quadratic time, by the server "
        "instrumentation of both planes, before authentication",
    ),
    "certifi": Floor(
        "2026.10.19",
        ("2026.7.22",),
        "GHSA-6q48-6c7w-9gw2: a trusted root whose intermediates' keys are compromised, in the "
        "trust store of every upstream and Vault connection",
    ),
    "djangorestframework": Floor(
        "3.18.2",
        ("3.17.1",),
        "GHSA-33wh-fxxf-88vv: an unbounded `Accept` header negotiated before authentication; "
        "GHSA-3547-9m27-7rxg: a compression codec as the charset inflates a body past its limit",
    ),
    "pyjwt": Floor(
        "2.15.0",
        ("2.13.0", "2.14.0"),
        "GHSA-9v7f-9g4p-ffgj: the key-set fetch followed redirects; GHSA-42vr-xj54-vc7v and "
        "GHSA-x33g-cr3x-6449, fixed in 2.15.0",
    ),
    "django": Floor(
        "6.0.8",
        ("6.0.7", "6.1.0"),
        "GHSA-q238-5cxm-5c9h, GHSA-wvqv-fj8w-qmhm: GeoDjango, which is not installed here",
    ),
    "sqlparse": Floor(
        "0.6.0",
        ("0.5.5",),
        "GHSA-prg7-hcfm-mfcr and four more: quadratic and backtracking parses of SQL text",
    ),
    "urllib3": Floor(
        "2.8.0",
        ("2.7.0",),
        "GHSA-vxq7-64xx-v4gw, GHSA-gh4c-6fx4-qh6g, GHSA-8988-9cw3-xx77: streaming decompression "
        "and proxy TLS",
    ),
    "mako": Floor(
        "1.4.2",
        ("1.3.12",),
        "CVE-2026-102991: a template path escaping its directory on Windows — not reachable here, "
        "and named by an image scanner reading the gateway",
    ),
}


def _locked() -> dict[str, Version]:
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {
        canonicalize_name(package["name"]): Version(package["version"])
        for package in lock["package"]
        if "version" in package
    }


def _declarations() -> dict[str, list[tuple[str, Requirement]]]:
    """Every requirement the workspace states, by package: the members' dependencies and the
    root's constraints, each with the file that states it."""
    found: dict[str, list[tuple[str, Requirement]]] = {}
    for path in PYPROJECTS:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        stated = [
            *data.get("project", {}).get("dependencies", []),
            *data.get("tool", {}).get("uv", {}).get("constraint-dependencies", []),
        ]
        for line in stated:
            requirement = Requirement(line)
            found.setdefault(canonicalize_name(requirement.name), []).append(
                (str(path.relative_to(ROOT)), requirement)
            )
    return found


def test_every_floor_names_a_release_its_own_advisories_leave_alone() -> None:
    """The table checked against itself, so a typo cannot make an entry vacuous."""
    for name, floor in FLOORS.items():
        for release in floor.affected:
            assert Version(release) != Version(floor.minimum), (name, release)


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_the_lock_holds_the_package_at_or_above_its_floor(name: str) -> None:
    floor = FLOORS[name]
    locked = _locked()

    assert name in locked, (
        f"{name} is no longer locked at all; if nothing installs it any more, remove its entry"
    )
    version = locked[name]
    assert version >= Version(floor.minimum) and str(version) not in floor.affected, (
        f"uv.lock pins {name} {version}, below {floor.minimum} — {floor.why}"
    )


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_the_workspace_declares_the_floor_so_a_re_lock_cannot_go_below_it(name: str) -> None:
    floor = FLOORS[name]
    declared = _declarations().get(name, [])

    assert declared, (
        f"nothing in the workspace states a floor for {name}; a re-lock may resolve it to any "
        f"release, an affected one included — {floor.why}. A package reached only through another "
        "one belongs in the root pyproject's `constraint-dependencies`."
    )
    for release in floor.affected:
        admitted_by_all = all(
            requirement.specifier.contains(release, prereleases=True)
            for _path, requirement in declared
        )
        assert not admitted_by_all, (
            f"{name} {release} is admitted by every declaration "
            f"({', '.join(f'{path}: {req}' for path, req in declared)}) — {floor.why}"
        )
