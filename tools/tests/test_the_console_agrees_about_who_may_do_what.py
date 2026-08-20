"""The console's copies of the role sets, held to the shared definitions.

`aira_common.roles` defines `INCIDENT_ROLES` and `OVERSIGHT_ROLES` as single definitions, and its
own comment says why: on 2026-08-07 a live round found `it-steuerung` able to stop traffic in the
gateway while Management refused it a global rule — **two planes, one question, two answers**,
because the predicate had been written by hand in both.

`core/auth/roles.ts` opens by naming that incident and then restates all three lists, because the
console cannot import Python. Its own docstring says what that costs: *"A console that restates the
list a third time is the same defect with a longer fuse: nothing fails when the server's list
changes, the screen simply starts offering, or withholding, the wrong thing."*

Nothing failed. The three lists happened to agree on 2026-08-20 and no test compared them, so the
sentence describing the danger was the only thing standing between the console and it — and a rule
only a reviewer enforces is one the next round breaks. This is the comparison, in the one language
that can read both sides.

**Both directions**, like the vocabulary check beside it: a role the console grants must be granted
by the server, and a role the server grants must be offered by the console. One direction catches
the console growing a permission the server refuses, which reads to a user as a broken button; the
other catches the console withholding one the server allows, which is a capability with no way in —
`FRD-206`'s defect, and the kind that does not announce itself.

These stay *console* predicates: they decide what to **offer**, and the server decides what happens.
That is the reason a disagreement is survivable, and not a reason to leave one in place.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "src"))

from aira_common.roles import INCIDENT_ROLES, OVERSIGHT_ROLES, Role  # noqa: E402

ROLES_TS = ROOT / "management" / "frontend" / "src" / "app" / "core" / "auth" / "roles.ts"


def _list(source: str, const: str) -> set[str]:
    """The string members of a `const NAME = ['a', 'b'];` array."""
    block = re.search(rf"const {const} = \[(.*?)\];", source, re.S)
    assert block, f"{const} is no longer an array literal in roles.ts — move this check with it"
    return set(re.findall(r"'([a-z-]+)'", block.group(1)))


def _slugs(roles: frozenset[Role]) -> set[str]:
    return {str(role) for role in roles}


def test_the_console_offers_the_kill_switch_to_exactly_the_incident_roles() -> None:
    """The set the live round found wrong, in the plane that found it wrong."""
    assert _list(ROLES_TS.read_text(), "INCIDENT_ROLES") == _slugs(INCIDENT_ROLES)


def test_the_console_shows_every_use_case_to_exactly_the_oversight_roles() -> None:
    """Wider than governance by exactly IT Security, and the console has to know which — a role
    that sees nothing is not a restricted view, it is an absent one."""
    assert _list(ROLES_TS.read_text(), "OVERSIGHT_ROLES") == _slugs(OVERSIGHT_ROLES)


def test_the_console_offers_standards_to_exactly_the_roles_that_may_write_them() -> None:
    """`SECURITY_ROLES` mirrors Management's `IsITSecurity`, which is `INCIDENT_ROLES` today.

    The console keeps it as a **separate** list on purpose — two questions with one answer — so the
    check is separate too. Folding them together here would make this test unable to see the day
    they stop coinciding, which is the only day either list matters.
    """
    from aira_management.roles import Role as ManagementRole  # noqa: PLC0415

    expected = {str(role) for role in (ManagementRole.GLOBAL_ADMIN, ManagementRole.IT_SECURITY)}
    assert _list(ROLES_TS.read_text(), "SECURITY_ROLES") == expected


def test_every_role_the_console_names_is_a_role() -> None:
    """A slug that is not in the vocabulary grants nothing and reads as though it did — the
    `token_spike` shape, one screen over."""
    named = set()
    for const in ("INCIDENT_ROLES", "OVERSIGHT_ROLES", "SECURITY_ROLES"):
        named |= _list(ROLES_TS.read_text(), const)

    assert named <= {str(role) for role in Role}
