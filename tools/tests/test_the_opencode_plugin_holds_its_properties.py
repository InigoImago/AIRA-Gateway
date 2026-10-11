"""The OpenCode budget plugin's own tests, run from the suite that gates this repository.

The plugin is JavaScript that runs inside a third-party client (`FRD-139` §5), so neither `pytest`
nor `ng test` covers it: it is not Python, and it is not part of the Angular application. Its
properties are asserted by `node --test` in `tools/opencode/plugin/*.test.mjs`.

This wrapper exists so those assertions are **not optional**. Two things follow from it:

- `make test-py` and CI run them, so a change to the plugin cannot land green without them;
- `tools/mutation_check.py` can break a property in the JavaScript and have a pytest run notice,
  which is the only way this project trusts a test at all.

**Node is not treated as optional here.** `ADR-0003` pins Node as part of this repository's
toolchain, so a missing `node` is a broken environment and not a reason to skip — a skipped test
reports success, which for a guard is the one outcome worse than failing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "tools" / "opencode" / "plugin"


def test_the_plugins_own_test_suite_passes() -> None:
    node = shutil.which("node")
    assert node, (
        "`node` is not on PATH. It is part of this repository's toolchain (`ADR-0003`), and the "
        "OpenCode plugin's properties are asserted by `node --test`. Skipping here would report "
        "success for a guard that never ran."
    )

    # The files are listed rather than the directory: `node --test <dir>` resolves the path as a
    # module in this version and dies before running anything.
    tests = sorted(str(path) for path in PLUGIN.glob("*.test.mjs"))
    assert tests, f"no plugin tests found in {PLUGIN}"

    result = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [node, "--test", "--test-reporter=tap", *tests],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )

    assert result.returncode == 0, (
        f"node --test failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    )
    # A runner that found no file exits 0 with nothing to say, which would make this guard a
    # decoration. The count is asserted loosely: the point is that the files were found and ran.
    assert "# pass " in result.stdout, result.stdout[-2000:]
    passed = int(result.stdout.split("# pass ")[1].split("\n")[0])
    assert passed >= 25, f"only {passed} plugin properties ran; the suite had 30"


def test_what_ships_is_one_file_that_imports_nothing() -> None:
    """OpenCode loads **every** file in a plugin directory as a plugin.

    Measured, with the client refusing to answer at all: the first version shipped the arithmetic
    in `budget-line.js` beside `aira-budget.js`, the loader handed both to the plugin API, the
    helper exported no plugin, and `opencode run` died with `UnknownError … Check server logs`.
    Nothing in the plugin was wrong; the *second file* was.

    A shipped artefact lives in somebody else's loader, where a neighbouring module is not a
    private detail. So this directory holds exactly one thing to install, and that file reaches
    for nothing next to it.
    """
    shipped = sorted(
        path.name for path in PLUGIN.glob("*.js") if not path.name.endswith(".test.js")
    )
    assert shipped == ["aira-budget.js"], (
        f"{shipped} would all be loaded as plugins by OpenCode. A helper beside the plugin is "
        "handed to the plugin API and takes the client down."
    )

    source = (PLUGIN / "aira-budget.js").read_text(encoding="utf-8")
    assert 'from "./' not in source, "a relative import is a second file by another name"


def test_the_file_exports_one_thing_and_it_is_a_plugin() -> None:
    """The loader calls **every** export as a plugin factory, whatever the types say.

    Both halves measured against `opencode 1.18.15`:

    - `export const id = "aira-budget"` — which `PluginModule.id` declares as allowed — makes it
      refuse the file outright: *"Plugin export is not a function"*. The plugin then silently does
      nothing, which for a budget display is the worst of the outcomes.
    - an exported helper is **called** with a plugin's arguments and its return value read as
      hooks, which ends in `plugin config hook failed` and a session that hangs.

    And it must be `server`, not `tui`: a file exporting `tui` is picked up by the server loader
    anyway and treated the same way. The route into the TUI's chrome is the toast endpoint the TUI
    itself serves (`client.tui.showToast`).
    """
    source = (PLUGIN / "aira-budget.js").read_text(encoding="utf-8")

    exports = re.findall(r"^export\s+(?:const|let|var|function|class)\s+(\w+)", source, re.M)
    assert exports == ["server"], f"exports {exports}; every one of them is called as a plugin"
    assert "export default" not in source, "a default export is an export like any other"
    assert "export {" not in source, "a re-export list is several exports"

    # The arithmetic is reachable for the tests without being exported.
    assert "server.internals = {" in source


def test_the_tests_run_against_the_file_that_ships() -> None:
    """A test that imports a copy proves something about the copy.

    Both test files import `./aira-budget.js` — the artefact a developer installs — rather than a
    re-export or a build output. Stated as a check because the obvious way to tidy the merge above
    is to split the arithmetic back out into a module the tests import, which is exactly the
    arrangement that broke the client.
    """
    for test in sorted(PLUGIN.glob("*.test.mjs")):
        # Every relative specifier in the file, however the formatter wrapped the import.
        local = set(re.findall(r'from "(\./[^"]+)"', test.read_text(encoding="utf-8")))
        assert local, f"{test.name} imports nothing from the plugin"
        assert local == {"./aira-budget.js"}, f"{test.name} imports {sorted(local)}, not it"
