"""The console's role lists are the server's, asked in TypeScript (`FRD-206`, `ADR-0017`).

`core/auth/roles.ts` exists because of a measured defect, and says so in its own first paragraph:
on 2026-08-07 a live round found `it-steuerung` able to stop traffic in the gateway while
Management refused it a global rule — **two planes, one question, two answers**, because the
predicate had been written by hand in both. The file's answer was to write the console's copy
*once*, and its warning is exactly the failure this test is the counterpart to:

    "A console that restates the list a third time is the same defect with a longer fuse: nothing
    fails when the server's list changes, the screen simply starts offering, or withholding, the
    wrong thing."

Nothing failed. There was no counterpart at all — the file stated a bound and nothing read it,
which is a shape `LESSONS.md` lists under *a named bound that nothing reads is a bound the module
claims and does not have*. And on 2026-08-27 a sweep found two components restating a list by hand
anyway (`model-catalog.canEdit`, `installation-budget-card.canManage`), inside a console whose
one-definition file says not to.

**A console predicate can never grant anything** — the server decides, every time, and a
disagreement shows up as a refusal rather than as access. What it costs is the other direction:
a screen that offers an action which then 403s reads as a broken system rather than as a boundary
(`FRD-206`), and a screen that hides one the server would allow is a capability with no way in,
which does not announce itself at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aira_common.roles import CATALOG_ROLES, INCIDENT_ROLES, OVERSIGHT_ROLES, Role

ROLES_TS = (
    Path(__file__).resolve().parents[2]
    / "management"
    / "frontend"
    / "src"
    / "app"
    / "core"
    / "auth"
    / "roles.ts"
)

#: `const NAME = ['a', 'b'];` — the only shape this file uses, and the assertion below fails
#: loudly rather than silently skipping if somebody writes the list a different way.
_LIST = re.compile(r"const (\w+) = \[([^\]]*)\];")


def _console_lists() -> dict[str, set[str]]:
    source = ROLES_TS.read_text(encoding="utf-8")
    found = {
        name: {value.strip().strip("'\"") for value in body.split(",") if value.strip()}
        for name, body in _LIST.findall(source)
    }
    assert found, f"no role lists parsed from {ROLES_TS} — has the shape changed?"
    return found


#: Console list → the server's definition of the same question.
#:
#: `SECURITY_ROLES` and `INSTALLATION_ROLES` have no shared constant on the server: they mirror the
#: DRF permission classes `IsITSecurity` and `IsGlobalAdmin`, whose `roles` tuples are the
#: definition. Read from those classes rather than retyped here, or this test becomes the fourth
#: copy of the thing it exists to stop.
def _server_sets() -> dict[str, set[str]]:
    from aira_management.rbac import IsGlobalAdmin, IsITSecurity

    return {
        "INCIDENT_ROLES": {str(role) for role in INCIDENT_ROLES},
        "OVERSIGHT_ROLES": {str(role) for role in OVERSIGHT_ROLES},
        "CATALOG_ROLES": {str(role) for role in CATALOG_ROLES},
        "SECURITY_ROLES": {str(role) for role in IsITSecurity.roles},
        "INSTALLATION_ROLES": {str(role) for role in IsGlobalAdmin.roles},
    }


@pytest.mark.parametrize("name", sorted(_server_sets()))
def test_a_console_role_list_is_the_servers(name: str) -> None:
    console = _console_lists()
    assert name in console, (
        f"{name} is gone from roles.ts. If the question it asked is gone too, remove it from this "
        "test as well — an entry defending a deleted rule reports green about nothing."
    )
    assert console[name] == _server_sets()[name], (
        f"{name} disagrees: the console says {sorted(console[name])} and the server says "
        f"{sorted(_server_sets()[name])}. The server decides, so this shows up as a screen "
        "offering an action that 403s, or withholding one it would allow."
    )


def test_every_role_the_console_names_is_a_role_the_server_has() -> None:
    """A typo in a console list is silent in exactly one direction.

    `['it-securty']` withholds a control from everybody and no test of either plane fails — the
    predicate simply never matches. Checked separately from the sets above so that the failure
    message says *this is not a role* rather than *these two lists differ*, which sends the reader
    to compare two correct-looking lines.
    """
    known = {str(role) for role in Role}
    for name, roles in _console_lists().items():
        unknown = sorted(roles - known)
        assert not unknown, f"{name} names {unknown}, which the server has never heard of"


def test_no_component_writes_a_role_out_by_hand() -> None:
    """One file decides, which is what `roles.ts` is for — and two components bypassed it.

    Found by a sweep on 2026-08-27: `model-catalog.canEdit` and
    `installation-budget-card.canManage` each read `roles.includes('global-admin')` directly. Both
    happened to agree with the server that day, which is what makes this worth a guard rather than
    a correction: nothing would have failed on the day they stopped.

    `roles.ts` itself is the one place a role slug may be written, and the spec files are exempt —
    a test naming the role it is about is the test being specific, not a second definition.

    **Templates too**, though none carries one today. A `@if (hasRole('it-security'))` in an
    `.html` file is the same decision in the same console, and a guard that reads only `.ts` would
    have watched the copy move rather than stopped it.
    """
    app = ROLES_TS.parents[3]
    written_out = re.compile(
        r"""(includes|===|!==)\s*\(?\s*['"](global-admin|it-security|it-steuerung)['"]"""
    )
    offenders: list[str] = []
    for path in sorted([*app.rglob("*.ts"), *app.rglob("*.html")]):
        if path == ROLES_TS or path.name.endswith(".spec.ts"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if written_out.search(line):
                offenders.append(f"{path.relative_to(app)}:{number}  {line.strip()[:90]}")

    assert not offenders, (
        "these decide authority from a role slug written on the spot; ask a predicate from "
        "`core/auth/roles.ts` instead, so the console has one answer per question:\n  "
        + "\n  ".join(offenders)
    )
