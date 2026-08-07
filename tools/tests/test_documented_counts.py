"""The numbers the documentation states about the harness are the harness's numbers.

Written after finding `CLAUDE.md` claiming **124** guarded properties while the harness had 220.
The interesting part is not the drift, it is *how* it survived: every update to that figure was a
string replacement whose anchor did not match, so each one silently changed nothing and reported
success. Six edits in a row, all no-ops, none of them checked.

That is the same failure this whole release has been about — an operation that is accepted, appears
to have worked, and did not happen — arriving in the documentation instead of in a request. So it
gets the same answer: make it fail loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "mutation_check.py"
CLAUDE = ROOT / "CLAUDE.md"

#: `Mutation(` at the start of a constructor call in the harness's list.
_MUTATION = re.compile(r"^\s*Mutation\($", re.MULTILINE)
#: The one sentence in `CLAUDE.md` that states the figure.
_CLAIM = re.compile(r"does this for \*\*(\d+) properties\*\*")


def harness_count() -> int:
    return len(_MUTATION.findall(HARNESS.read_text()))


def test_the_harness_has_mutations_to_count() -> None:
    """A guard on the guard: if the pattern stops matching, the check below passes vacuously by
    comparing zero to zero and quietly stops existing."""
    assert harness_count() > 100


def test_claude_md_states_the_number_of_properties_the_harness_actually_guards() -> None:
    match = _CLAIM.search(CLAUDE.read_text())
    assert match is not None, (
        "CLAUDE.md no longer states a property count. If the sentence moved, move this check with "
        "it — deleting it is how the figure goes stale again."
    )

    claimed = int(match.group(1))
    actual = harness_count()

    assert claimed == actual, (
        f"CLAUDE.md claims {claimed} guarded properties; the harness defines {actual}. "
        "The figure is a claim about how much of this system is checked, so a stale one overstates "
        "or understates exactly the thing a reader is relying on."
    )


@pytest.mark.parametrize("identifier", ["Z10", "Y1", "P1"])
def test_a_named_mutation_the_documentation_refers_to_still_exists(identifier: str) -> None:
    """The FRDs cite mutations by name. A citation pointing at nothing is worse than none — it
    reads as evidence."""
    assert f'"{identifier}"' in HARNESS.read_text()
