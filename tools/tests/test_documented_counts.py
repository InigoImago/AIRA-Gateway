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


def test_every_mutation_has_an_identifier_of_its_own() -> None:
    """Found while adding a mutation and reusing an id that already existed.

    The id is only a label — every entry runs regardless — so the *checking* was sound. The
    **reporting** was not: "N3 survived" named two unrelated properties, and a summary that sends
    somebody to the wrong line is worse than one that says nothing. Thirty-eight had accumulated.

    Later duplicates were renamed rather than the first, because `CLAUDE.md` and the DEVLOG cite
    ids by name and renaming a cited one breaks the prose that explains why the property exists.
    """
    import collections
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "tools/mutation_check.py").read_text()
    ids = re.findall(r'^    Mutation\(\n        "([A-Za-z0-9]+)"', source, re.M)
    assert ids, "no mutations found — the pattern stopped matching"

    duplicates = sorted(i for i, n in collections.Counter(ids).items() if n > 1)
    assert not duplicates, (
        f"these ids name more than one property: {', '.join(duplicates)}. A report that says "
        "'X survived' would be ambiguous about which."
    )


def test_every_make_target_the_reader_facing_docs_name_exists() -> None:
    """A documented command that does not exist is the ESLint shape, in a smaller frame.

    `FRD-123` promised `make verify-local`; the target is `verify-up`, and a developer following
    the document gets *"No rule to make target"* and no clue which of the twenty-odd targets was
    meant. Four more of these were found in one afternoon in August and fixed one at a time —
    which is the sign that a check is missing rather than that people are careless.

    `DEVLOG.md` and `LESSONS.md` are deliberately excluded: both quote dead targets *because* they
    were dead, and rewriting the story to keep a checker happy would delete the record.
    """
    import re

    makefile = (ROOT / "Makefile").read_text()
    targets = set(re.findall(r"^([a-z][a-z0-9_-]*):", makefile, re.M))

    missing: list[str] = []
    for path in sorted(ROOT.glob("docs/**/*.md")) + [ROOT / "README.md", ROOT / "CLAUDE.md"]:
        if path.name in {"DEVLOG.md", "LESSONS.md"}:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for named in re.findall(r"`make ([a-z][a-z0-9_-]*)`", line):
                if named not in targets:
                    missing.append(f"{path.relative_to(ROOT)}:{number}  make {named}")

    assert not missing, (
        "These documents name a `make` target that does not exist:\n  " + "\n  ".join(missing)
    )
