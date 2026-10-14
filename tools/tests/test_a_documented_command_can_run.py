"""Every `python -m <module>` the documentation tells a reader to run names a module that runs.

**Why this exists.** `docs/ARCHITECTURE.md` gave the configuration consumer's command as
`python -m aira_gateway.consumer` — a package with no `__main__`, so the command exits at once with
"cannot be directly executed". Compose runs `python -m aira_gateway.consumer.worker`, which is why
nothing in the stack noticed; a deployment written from the document's table would have had a
consumer in a crash loop, and with it no configuration change and no key revocation reaching the
gateway. `docs/features/FRD-002` named a seed module, `gateway.seed`, that has never existed.

`test_documentation_links.py` already holds every `make` target a document names to the Makefile.
This is the same check for the other way a document tells somebody to start something.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The pages a reader follows. The development log and the lessons are history: a command there is
#: what was run then, and may be quoted there precisely because it was wrong.
PAGES = sorted(
    {
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CLAUDE.md",
        ROOT / "config" / "README.md",
        ROOT / "deploy" / "compose" / "README.md",
        ROOT / "e2e" / "README.md",
        ROOT / "tests" / "integration" / "README.md",
        ROOT / "management" / "frontend" / "README.md",
        *(ROOT / "tools").rglob("README.md"),
        *(ROOT / "docs").rglob("*.md"),
    }
    - {ROOT / "docs" / "DEVLOG.md", ROOT / "docs" / "LESSONS.md"}
)

_RUN = re.compile(r"\bpython3? -m ([A-Za-z_][A-Za-z0-9_.]*[A-Za-z0-9_])")

#: Top-level names that are this repository's. Anything else — `pytest`, `http.server` — is
#: somebody else's module, and whether it runs is not a claim this repository makes.
OURS = ("aira_gateway", "aira_management", "aira_common", "gateway", "management", "libs", "tools")


def _commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for page in PAGES:
        if not page.exists():
            continue
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            for module in _RUN.findall(line):
                if module.split(".")[0] in OURS:
                    found.append((f"{page.relative_to(ROOT)}:{number}", module))
    return found


def test_the_documents_name_commands_to_check() -> None:
    """A guard on the guard: a pattern that matched nothing would leave nothing to fail on."""
    assert len(_commands()) >= 3, _commands()


@pytest.mark.parametrize(("where", "module"), _commands())
def test_a_documented_module_can_be_run(where: str, module: str) -> None:
    spec = importlib.util.find_spec(module)
    assert spec is not None, f"{where}: `python -m {module}` names a module that does not exist"
    if spec.submodule_search_locations is not None:
        main = importlib.util.find_spec(f"{module}.__main__")
        assert main is not None, (
            f"{where}: `python -m {module}` names a package with no `__main__` — Python refuses "
            "to run it. Name the module that has the entry point."
        )
        return
    assert spec.origin is not None
    source = Path(spec.origin).read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in source, (
        f'{where}: `python -m {module}` runs a module with no `if __name__ == "__main__"` '
        "block, which imports it and does nothing."
    )
