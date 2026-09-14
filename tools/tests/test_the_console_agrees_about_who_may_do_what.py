"""The console asks the server's permissions, in the server's words (`FRD-614`, `ADR-0025`).

The console cannot import Python, so `core/auth/roles.ts` restates the permission catalogue, and
this holds that copy to `aira_common.permissions` in both directions. A name the console asks that
the server never lists withholds a control from everybody; a name the server lists that the console
lacks is a capability with no way in. Neither announces itself (`FRD-206`).

And no console file decides from a role slug: what a role may do is data on the server, so a role
written into the console is a second definition that goes wrong silently the day a role changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "src"))

from aira_common.permissions import CATALOGUE, Permission  # noqa: E402

ROLES_TS = ROOT / "management" / "frontend" / "src" / "app" / "core" / "auth" / "roles.ts"
APP = ROLES_TS.parents[2]

ROLE_SLUGS = ("global-admin", "it-security", "it-steuerung")

#: A role slug compared on the spot — in a component or a template alike.
WRITTEN_OUT = re.compile(
    r"""(includes|===|!==)\s*\(?\s*['"](""" + "|".join(ROLE_SLUGS) + r""")['"]"""
)


def _console_permissions() -> list[str]:
    """The members of `export const PERMISSIONS = [...] as const;`, in order."""
    source = ROLES_TS.read_text(encoding="utf-8")
    block = re.search(r"export const PERMISSIONS = \[(.*?)\] as const;", source, re.S)
    assert block, "PERMISSIONS is no longer an array literal in roles.ts — move this check with it"
    found = re.findall(r"'([^']*)'", block.group(1))
    assert found, "no permissions parsed from roles.ts — has the shape changed?"
    return found


def test_every_permission_the_console_asks_is_one_the_server_has() -> None:
    known = {str(permission) for permission in Permission}
    unknown = [name for name in _console_permissions() if name not in known]

    assert not unknown, f"roles.ts names {unknown}, which the server has never heard of"


def test_every_permission_the_server_has_is_one_the_console_knows() -> None:
    console = set(_console_permissions())
    missing = [str(permission) for permission in Permission if str(permission) not in console]

    assert not missing, f"the server lists {missing}, and the console cannot ask for them"


def test_the_console_lists_them_in_the_catalogues_order() -> None:
    """The order the console shows them in, grouped by area — and a duplicate shows up here too."""
    assert _console_permissions() == [str(permission) for permission in CATALOGUE]


def test_roles_ts_holds_no_role_and_no_role_set() -> None:
    source = ROLES_TS.read_text(encoding="utf-8")

    assert not re.findall(r"\b[A-Z_]+_ROLES\b", source), "a role-set constant is back in roles.ts"
    assert not [slug for slug in ROLE_SLUGS if slug in source], "roles.ts names a role"


def test_the_guard_sees_a_role_slug_in_either_form() -> None:
    """A guard that cannot fail is the thing it guards against."""
    assert WRITTEN_OUT.search("this.me()?.roles.includes('global-admin') ?? false")
    assert WRITTEN_OUT.search("@if (me()?.roles?.includes('it-security')) {")
    assert WRITTEN_OUT.search("role === 'it-steuerung'")
    assert not WRITTEN_OUT.search("can(this.me(), 'catalog.write')")


def test_no_console_file_decides_from_a_role_slug() -> None:
    """Every `.ts` and `.html` under the app, `roles.ts` included. A spec may name the role it is
    about: that is the test being specific, not a second definition."""
    offenders: list[str] = []
    for path in sorted([*APP.rglob("*.ts"), *APP.rglob("*.html")]):
        if path.name.endswith(".spec.ts"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if WRITTEN_OUT.search(line):
                offenders.append(f"{path.relative_to(APP)}:{number}  {line.strip()[:90]}")

    assert not offenders, (
        "these decide authority from a role slug; ask `can()` from `core/auth/roles.ts` for the "
        "permission instead:\n  " + "\n  ".join(offenders)
    )
