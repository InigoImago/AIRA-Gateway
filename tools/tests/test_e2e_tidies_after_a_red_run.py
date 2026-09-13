"""A red browser run still purges what it retired.

`make test-e2e` runs the suite, then `purge-e2e-use-cases`, then returns the suite's status. A use
case the suite retired keeps its slug until it is purged (`FRD-607`). A run that skips the purge
therefore leaves a slug the next run cannot create again; that run fails, skips the purge in turn,
and the suite stays red for a reason that has nothing to do with the code under test.

The target is run, not read: whether a failure reaches the purge is a fact about the shell, not
about the text of the recipe. `npm`, `npx` and `docker` are stand-ins on `PATH`, so nothing is
installed, nothing is tested and nothing is purged.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Make's own variables, dropped so an outer `make` (`make test`) passes no flags to the inner one.
MAKE_VARIABLES = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")


def _stand_in(directory: pathlib.Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_the_target(
    tmp_path: pathlib.Path, suite_exit: int
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    register = tmp_path / "e2e" / ".artifacts" / "use-cases.txt"
    register.parent.mkdir(parents=True)
    register.write_text("probe-uc\n")

    calls = tmp_path / "docker-calls.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stand_in(bin_dir, "npm", "exit 0")
    _stand_in(bin_dir, "npx", f"exit {suite_exit}")
    _stand_in(bin_dir, "docker", f'echo "$*" >> "{calls}"')

    env = {key: value for key, value in os.environ.items() if key not in MAKE_VARIABLES}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["make", "--no-print-directory", "-C", str(tmp_path), "test-e2e"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, calls


@pytest.mark.parametrize("suite_exit", [1, 0], ids=["red", "green"])
def test_the_purge_runs_whatever_the_suite_returned(
    tmp_path: pathlib.Path, suite_exit: int
) -> None:
    result, calls = _run_the_target(tmp_path, suite_exit)
    output = result.stdout + result.stderr

    assert calls.exists(), f"the purge never ran after a {suite_exit} from the suite:\n{output}"
    purge = calls.read_text()
    assert "purge_test_use_cases" in purge
    assert "probe-uc" in purge, "the purge was not given the register's slugs"
    # The suite's verdict, not the purge's: a red suite is a red target, a green one green.
    assert (result.returncode == 0) == (suite_exit == 0), output
