"""Every field a log call writes is in the register, read from the code (`FRD-631` FR-7).

The register is an allow-list, so a field nobody registered is withheld at run time — safe, and
a line that has silently lost what its author meant it to say. This test reads every log call in
the three source trees with `ast` and fails on a field the register does not know, naming the file
and the line, so the author registers the field — with its kind, which is the decision that matters
— before anybody reads a log that is missing it.

What it reads:

- a logger's ``debug`` … ``critical`` with the event as its first argument;
- ``bind(...)`` on a logger;
- ``watch(...)``, ``report(...)`` and a watched call's ``note(...)`` from
  `aira_common.integration_debug`, whose fields become a line too.

**A call that spreads a mapping it built (``**fields``) cannot be read this way**, and is listed in
:data:`SPREAD` with the test that renders its line and finds nothing withheld. A spread that is not
listed fails here — a scan that quietly narrows its own scope reports green about the part it kept.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aira_common.logging import KNOWN
from aira_common.logging.fields import IN_FLIGHT

ROOT = Path(__file__).resolve().parents[2]
TREES = (
    ROOT / "gateway" / "src",
    ROOT / "management" / "backend" / "src",
    ROOT / "libs" / "src",
    ROOT / "gateway" / "migrations",
)

LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical"})
WATCHED = frozenset({"watch", "report"})

#: The calls that hand a mapping over with ``**`` — `(file, function)` — and what reads them.
SPREAD: dict[tuple[str, str], str] = {
    ("aira_common/integration_debug.py", "report"): "test_integration_debug.py",
    ("aira_common/integration_debug.py", "watch"): "test_integration_debug.py",
    ("aira_common/observability/export.py", "export"): (
        "test_a_line_about_otel_does_not_travel_by_otel.py"
    ),
    ("aira_common/logging/access.py", "write_access_line"): "test_one_access_line_per_request.py",
    ("aira_common/logging/refusals.py", "say_refused"): "test_a_refused_sign_in_is_a_line.py",
    ("aira_gateway/persistence/writer.py", "say_recorded"): "test_every_request_is_a_line.py",
    ("aira_gateway/exception_handlers.py", "_handle_unexpected"): (
        "test_an_unexpected_error_says_where_and_not_what.py"
    ),
    ("aira_management/apps/api/exceptions.py", "_unexpected"): (
        "test_management_says_what_it_does.py"
    ),
    ("aira_management/apps/history/recorder.py", "<lambda>"): "test_configuration_history.py",
}


def _sources() -> list[Path]:
    return sorted(path for tree in TREES for path in tree.rglob("*.py"))


def _relative(path: Path) -> str:
    text = path.as_posix()
    for marker in ("/aira_gateway/", "/aira_management/", "/aira_common/", "/migrations/"):
        at = text.find(marker)
        if at >= 0:
            return text[at + 1 :]
    return text


def _imports_integration_debug(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "aira_common.integration_debug"
        for node in ast.walk(tree)
    )


def _calls(tree: ast.Module, path: Path) -> list[tuple[ast.Call, str]]:
    """Every call that writes fields into a line, with the function it sits in."""
    found: list[tuple[ast.Call, str]] = []
    watched = _imports_integration_debug(tree) or path.name == "integration_debug.py"

    def visit(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = inside
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = child.name
            elif isinstance(child, ast.Lambda):
                name = "<lambda>"
            if isinstance(child, ast.Call):
                func = child.func
                attr = func.attr if isinstance(func, ast.Attribute) else None
                bare = func.id if isinstance(func, ast.Name) else None
                first = child.args[0] if child.args else None
                # The event, as a literal or as a module's constant for it (`EVENT`).
                is_event = (isinstance(first, ast.Constant) and isinstance(first.value, str)) or (
                    isinstance(first, ast.Name) and first.id.isupper()
                )
                logged = (attr in LOG_METHODS and is_event) or attr == "bind"
                if logged or (watched and (bare in WATCHED or attr == "note")):
                    found.append((child, name))
            visit(child, name)

    visit(tree, "<module>")
    return found


def _offences() -> tuple[list[str], set[tuple[str, str]]]:
    unknown: list[str] = []
    spreads: set[tuple[str, str]] = set()
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call, inside in _calls(tree, path):
            for keyword in call.keywords:
                if keyword.arg is None:
                    spreads.add((_relative(path), inside))
                elif keyword.arg not in KNOWN and keyword.arg not in IN_FLIGHT:
                    unknown.append(f"{_relative(path)}:{call.lineno} writes `{keyword.arg}`")
    return unknown, spreads


def test_every_field_a_log_call_writes_is_registered() -> None:
    unknown, _spreads = _offences()
    assert not unknown, (
        "These fields are in no register, so every line would leave without them. Add each to "
        "`aira_common/logging/fields.py` with its kind — plain, person or address — its type and "
        "its meaning:\n" + "\n".join(unknown)
    )


def test_every_call_that_spreads_a_mapping_is_known_and_read_elsewhere() -> None:
    _unknown, spreads = _offences()
    unlisted = sorted(spreads - set(SPREAD))
    assert not unlisted, (
        f"These calls hand a line a mapping this test cannot read: {unlisted}. Add each to SPREAD "
        "with the test that renders its line and finds nothing withheld."
    )


@pytest.mark.parametrize(("site", "reader"), sorted(SPREAD.items()))
def test_a_listed_spread_still_exists_and_so_does_its_reader(
    site: tuple[str, str], reader: str
) -> None:
    """A list of exemptions nothing checks goes stale and keeps exempting."""
    _unknown, spreads = _offences()
    assert site in spreads, f"{site} spreads nothing any more: take it off SPREAD"
    assert any(path.name == reader for path in ROOT.glob("**/tests/*.py")), reader
