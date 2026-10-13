"""The images do not run Python with assertions switched off.

There are a couple of dozen `assert` statements in the gateway's and Management's production code
— **deliberately not a figure**, because a count in a docstring is a claim nothing keeps true, and
this one had already drifted before anybody noticed. Most of them narrow a type for mypy after a
check that already happened, and one is load-bearing enough to say so in its own comment:
`budgets/service.py` notes that a scope which does not bind its caller *"would have failed the
assertion below rather than mixing two people's counters, **but only because it is asserted**"*.

`python -O` and `PYTHONOPTIMIZE` remove every one of them. That is not a hypothetical setting: it
is a common "make it faster in production" reflex, it can be set in a base image nobody in this
repository wrote, and it changes no behaviour visibly — the process starts, serves, and has
silently lost a set of checks. **Undeclared is not permitted** is this project's rule about model
capabilities; the same reading applies to an assumption about the interpreter.

Verified rather than assumed, because the assumption is what makes those statements
acceptable. If a deployment ever wants the optimiser, this test is what makes that a **decision** —
somebody has to delete it, and then convert the load-bearing assertions into real checks first.

Deliberately not a rewrite of them. Turning type-narrowing assertions into `if ... raise`
would add branches no test can reach and no reader benefits from; the honest fix is to keep them
and to keep the condition they rely on true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

#: How the optimiser gets switched on: the flag, or the environment variable the interpreter reads
#: on startup. `PYTHONOPTIMIZE=0` is explicitly *off* and is left alone — the pattern demands a
#: value, so "somebody wrote it down as off" is not reported as somebody turning it on.
#:
#: Every spelling a start-up file uses: a versioned interpreter (`python3.14 -O`), the flag among
#: others (`-OO`, `-u -O`), the **exec form** (`CMD ["python", "-O", …]`), and Docker's legacy
#: `ENV PYTHONOPTIMIZE 1` without `=`. A flag after `-m <module>` belongs to the module.
_FLAG = re.compile(
    r"\bpython[\d.]*\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*O"
    r"|[\"']python[\d.]*[\"']\s*,\s*(?:[\"']-[A-Za-z]*[\"']\s*,\s*)*[\"']-[A-Za-z]*O"
    r"|PYTHONOPTIMIZE[\"']?\s*[=:\s]\s*[\"']?[1-9]"
)


def _commands(path: Path) -> list[str]:
    """A YAML file's `command`/`entrypoint` lists, each as one line — the form `_FLAG` reads.

    A Compose list puts the interpreter and its flags on lines of their own (`- python`, `- -O`),
    which no line-by-line pattern can see.
    """

    class _AnyTag(yaml.SafeLoader):
        pass

    _AnyTag.add_multi_constructor("!", lambda loader, suffix, node: None)
    try:
        documents = list(yaml.load_all(path.read_text(errors="ignore"), Loader=_AnyTag))
    except yaml.YAMLError:
        return []
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("command", "entrypoint") and isinstance(value, list):
                    found.append(" ".join(str(item) for item in value))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for document in documents:
        walk(document)
    return found


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
    offenders += [
        f"{path.relative_to(ROOT)}: {command}"
        for path in _startup_files()
        if path.suffix in (".yml", ".yaml")
        for command in _commands(path)
        if _FLAG.search(command)
    ]

    assert not offenders, (
        "these start Python with assertions optimised away:\n  "
        + "\n  ".join(offenders)
        + "\n\nProduction code here relies on `assert` — including one that keeps two people's "
        "budget counters apart — and `-O` removes every one of them silently. Either drop the "
        "flag, or convert the load-bearing assertions into real checks first and then delete this "
        "test deliberately."
    )


@pytest.mark.parametrize(
    "spelling",
    [
        "python -O -m aira_gateway",
        "python3.14 -O app.py",
        "RUN python -OO setup.py",
        "exec python -u -O main.py",
        'CMD ["python", "-O", "-m", "uvicorn"]',
        "ENTRYPOINT ['python3', '-u', '-O', 'main.py']",
        "ENV PYTHONOPTIMIZE 1",
        "ENV PYTHONOPTIMIZE=2",
        "PYTHONOPTIMIZE: '1'",
    ],
)
def test_every_spelling_of_the_optimiser_is_found(spelling: str) -> None:
    assert _FLAG.search(spelling), spelling


@pytest.mark.parametrize(
    "harmless",
    ["PYTHONOPTIMIZE=0", "python -m pytest -q", "python -Wd -m app", "uv run python tools/x.py"],
)
def test_a_line_that_does_not_switch_it_on_is_left_alone(harmless: str) -> None:
    assert not _FLAG.search(harmless), harmless


def test_a_compose_list_is_read_as_one_command(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  app:\n    command:\n      - python\n      - -O\n      - main.py\n"
    )

    assert [c for c in _commands(compose) if _FLAG.search(c)] == ["python -O main.py"]
