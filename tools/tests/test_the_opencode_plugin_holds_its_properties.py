"""The OpenCode budget plugin's own tests, run from the suite that gates the repository (`FRD-144`).

The plugin is a Solid component file that runs inside a third-party client, so neither `pytest` nor
`ng test` covers it. Its properties are asserted by `bun test` in
`tools/clients/opencode-budget-plugin.test.ts`, under the runtime OpenCode itself runs plugins on —
Bun, with `solid-js` and `@opentui/*` resolved as OpenCode resolves them, and OpenTUI's test
renderer drawing the frame a developer reads.

This wrapper exists so those assertions are **not optional**: `make test-clients` and CI run it,
and `tools/mutation_check.py` can break a property in the plugin and have a pytest run notice.

Bun comes from `tools/clients` (`npm ci` there), like the clients it is tested beside. Without that
install the test is skipped — except where `AIRA_REQUIRE_REAL_CLIENTS` is set, as in CI, which
makes a skip a failure: a skipped guard reports success.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLIENTS = ROOT / "tools" / "clients"
BUN = CLIENTS / "node_modules" / ".bin" / "bun"
PLUGINS = ROOT / "tools" / "opencode" / "plugins"
PLUGIN = PLUGINS / "aira-budget" / "tui.tsx"


@pytest.fixture
def bun() -> Path:
    if BUN.exists():
        return BUN
    if os.environ.get("AIRA_REQUIRE_REAL_CLIENTS"):
        pytest.fail("Bun is required here and not installed: make test-clients")
    pytest.skip("Bun is not installed (npm ci in tools/clients)")


def test_the_plugins_own_test_suite_passes(bun: Path) -> None:
    result = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [
            str(bun),
            "test",
            # Solid's client build, as OpenCode loads it; the default is its server renderer.
            "--conditions=browser",
            "--preload",
            "./preload-opencode-plugin.ts",
            "./opencode-budget-plugin.test.ts",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=CLIENTS,
    )
    report = result.stdout + result.stderr
    assert result.returncode == 0, f"bun test failed:\n{report[-6000:]}"
    # A runner that found no file exits 0 with nothing to say, which would make this guard a
    # decoration. The count is asserted loosely: the point is that the file was found and ran.
    passed = re.search(r"(\d+) pass", report)
    assert passed and int(passed.group(1)) >= 30, report[-2000:]


def test_what_ships_is_one_file_in_a_directory_of_its_own() -> None:
    """The install is a copy of one directory holding one file, and each generation finds it there.

    OpenCode 2 discovers a plugin *directory* under `.opencode/plugins/` and loads its `tui` entry.
    OpenCode 1 loads every `*.ts` and `*.js` directly in `plugin/` or `plugins/` as a **server**
    plugin, and a TUI module loaded that way hangs the session — measured. A directory keeps the
    file out of that scan, and `.tsx` would too. A second file beside it would be one more thing a
    loader might pick up, and one more a developer has to copy.
    """
    assert sorted(path.name for path in PLUGINS.iterdir()) == ["aira-budget"]
    assert sorted(path.name for path in (PLUGINS / "aira-budget").iterdir()) == ["tui.tsx"]
    assert 'from "./' not in PLUGIN.read_text(encoding="utf-8"), (
        "a relative import is a second file"
    )


def test_the_tests_run_against_the_file_that_ships() -> None:
    """A test that imports a copy proves something about the copy."""
    tests = (CLIENTS / "opencode-budget-plugin.test.ts").read_text(encoding="utf-8")
    assert '"../opencode/plugins/aira-budget/tui.tsx"' in tests
