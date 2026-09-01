"""`docs/ROLES.md` describes the roles this system has, and where they come from.

The document a reader opens to answer *"who may do what"* said, until 2026-08-31, that there were
**five** roles and that both planes read them from `realm_access.roles`. There are three, and
neither plane reads that claim — `ADR-0017` made group membership the only source of a role on
2026-08-09. `libs/src/aira_common/roles.py` opens by naming that exact paragraph as a defect it had
already corrected *in the module*; the correction never reached the document, and §5 went on
telling an operator setting up their own realm to assign realm roles. That grants nothing, reports
nothing, and leaves an installation where nobody holds a role.

`LESSONS.md` §1: **correct the definition, then grep for the comparison.** This is the comparison,
written against the module that owns the answer rather than against a list typed here — a second
list would go stale in exactly the way the first one did.

**Scoped to the table and the headings, not to the prose**, and that is deliberate. The document
*should* say that `use-case-admin` and `use-case-user` were roles until `ADR-0017`, because a
reader who remembers them needs to be told where they went; a check that forbade the words outright
would fire on the supported case and be the one nobody reads on the day it is right (`LESSONS.md`
§3). What a reader takes a list of roles *from* is the table.
"""

from __future__ import annotations

import re
from pathlib import Path

from aira_common.roles import ALL_ROLES

ROLES_DOC = Path(__file__).resolve().parents[2] / "docs" / "ROLES.md"

#: Names that were roles until `ADR-0017` and are grants on one use case now.
ABOLISHED = ("use-case-admin", "use-case-user")


def _text() -> str:
    return ROLES_DOC.read_text()


def _role_table() -> list[str]:
    """The data rows of the table in §1 — the list a reader would copy.

    Located by the section heading rather than by a line number, and stopping at the first
    sub-heading: §1 carries a **second** table — the three predicate sets — and counting both as
    roles is how a guard comes to report seven of three. The separator and header rows are dropped
    so the count is the number of roles rather than the number of lines.
    """
    lines = _text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 1."))
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("#" * 2)),
        len(lines),
    )
    rows = [line for line in lines[start:end] if line.startswith("|")]
    return [row for row in rows if not row.startswith("| ---") and "| Role " not in row]


def test_there_is_a_table_to_check() -> None:
    """A guard on the guard: a renamed heading would make every assertion below pass by finding
    nothing, which is how an absence check goes vacuous (`LESSONS.md` §7)."""
    assert len(_role_table()) >= 2


def test_the_table_lists_exactly_the_roles_the_code_has() -> None:
    rows = _role_table()
    missing = [str(role) for role in ALL_ROLES if not any(f"`{role}`" in row for row in rows)]
    assert not missing, f"docs/ROLES.md does not list {missing}"
    assert len(rows) == len(ALL_ROLES), (
        f"docs/ROLES.md lists {len(rows)} roles and the code has {len(ALL_ROLES)}"
    )


def test_the_table_lists_no_role_the_code_does_not_have() -> None:
    """The direction that actually failed. A document still listing an abolished role describes a
    badge somebody can be given that does nothing, which `FRD-206` calls worse than an absent one.
    Naming them in prose as *former* roles is right and is not what this looks at."""
    rows = _role_table()
    listed = [name for name in ABOLISHED if any(name in row for row in rows)]
    assert not listed, (
        f"docs/ROLES.md lists {listed} as roles; administering or belonging to a use case is a "
        "grant on that use case (ADR-0017), not a role"
    )


def test_no_heading_miscounts_the_roles() -> None:
    """*The five realm roles* was a **heading**, above a table of three. A number in a heading is
    the part a reader takes away and the part nobody re-reads."""
    headings = [line for line in _text().splitlines() if line.startswith("#")]
    wrong = [
        line
        for line in headings
        if re.search(r"\b(?:five|four|two) (?:realm )?roles\b", line, flags=re.IGNORECASE)
    ]
    assert not wrong, f"docs/ROLES.md heading says {wrong}; there are {len(ALL_ROLES)}"


def test_the_document_says_where_a_role_comes_from() -> None:
    """The mechanism, not the store. An operator following this document has to end up setting
    `AIRA_ROLE_GROUPS` and putting somebody in a group — the two things that actually confer a
    role — rather than assigning a realm role that grants nothing."""
    text = _text()
    assert "AIRA_ROLE_GROUPS" in text, (
        "docs/ROLES.md never names the setting that maps a group to a role, so a reader cannot "
        "act on it"
    )
    assert "grants nothing" in text, (
        "docs/ROLES.md does not say that a Keycloak realm role grants nothing — which is the one "
        "thing a reader coming from a realm-role installation has to be told (ADR-0017)"
    )
