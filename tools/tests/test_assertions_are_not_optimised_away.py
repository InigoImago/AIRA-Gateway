"""The images do not run Python with assertions switched off.

There are twenty `assert` statements in the gateway's and Management's production code. Most narrow
a type for mypy after a check that already happened, and one is load-bearing enough to say so in
its own comment — `budgets/service.py` notes that a scope which does not bind its caller *"would
have failed the assertion below rather than mixing two people's counters, **but only because it is
asserted**"*.

`python -O` and `PYTHONOPTIMIZE` remove every one of them. That is not a hypothetical setting: it
is a common "make it faster in production" reflex, it can be set in a base image nobody in this
repository wrote, and it changes no behaviour visibly — the process starts, serves, and has
silently lost a set of checks. **Undeclared is not permitted** is this project's rule about model
capabilities; the same reading applies to an assumption about the interpreter.

Verified rather than assumed, because the assumption is what makes those twenty statements
acceptable. If a deployment ever wants the optimiser, this test is what makes that a **decision** —
somebody has to delete it, and then convert the load-bearing assertions into real checks first.

Deliberately not a rewrite of the twenty. Turning type-narrowing assertions into `if ... raise`
would add branches no test can reach and no reader benefits from; the honest fix is to keep them
and to keep the condition they rely on true.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: How the optimiser gets switched on: the flag, or the environment variable the interpreter reads
#: on startup. `PYTHONOPTIMIZE=0` is explicitly *off* and is left alone — the pattern demands a
#: value, so "somebody wrote it down as off" is not reported as somebody turning it on.
_FLAG = re.compile(r"python3?\s+-O|PYTHONOPTIMIZE\s*[=:]\s*[\"']?[1-9]")


#: Everything that decides how a container starts. A `Dockerfile` sets it directly; a compose file
#: can set it in `environment:`; the `Makefile` is how it is run on a laptop and in CI.
def _startup_files() -> list[Path]:
    files = [
        path
        for pattern in ("Dockerfile*", "*.yml", "*.yaml")
        for path in ROOT.rglob(pattern)
        if "node_modules" not in path.parts
        and ".venv" not in path.parts
        and ".git" not in path.parts
    ]
    files.append(ROOT / "Makefile")
    return [path for path in files if path.is_file()]


def test_there_are_startup_files_to_check() -> None:
    """The guard's own failure mode: a glob that matches nothing passes the assertion below. This
    project has shipped two guards that could not fail, and both were silently green."""
    files = _startup_files()
    names = {path.name for path in files}

    assert len(files) >= 5, f"only found {sorted(names)}"
    assert "Dockerfile" in names
    assert "Makefile" in names


def test_nothing_starts_python_with_assertions_disabled() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in _startup_files()
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1)
        if _FLAG.search(line)
    ]

    assert not offenders, (
        "these start Python with assertions optimised away:\n  "
        + "\n  ".join(offenders)
        + "\n\nProduction code here relies on `assert` — including one that keeps two people's "
        "budget counters apart — and `-O` removes every one of them silently. Either drop the "
        "flag, or convert the load-bearing assertions into real checks first and then delete this "
        "test deliberately."
    )
