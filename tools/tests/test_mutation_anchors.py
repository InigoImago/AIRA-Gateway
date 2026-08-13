"""Every guarded property still points at code that exists, in exactly one place.

**Why this is a unit test and not part of `make mutants`.** The harness already reports a stale
anchor — it prints `STALE` and returns non-zero. But a full run applies 406 edits and runs a pytest
selection for each, which takes hours, so it is not in CI and is not run casually. The one
invariant that decides whether the whole harness means anything therefore had **no fast check at
all**, and it rotted: on 2026-08-12 ten anchors named code that had moved or been deleted, and
three matched more than one place. Nothing failed anywhere, because the only thing that would have
noticed was the thing nobody runs.

What that costs is specific, and it is the harness's own argument turned against it. A mutation is
a written claim that some property is defended by a test. A stale one **still reads as that claim**
— it sits in the file, it is cited by name in `CLAUDE.md` and the FRDs — while defending nothing.
`O2` had been vouching for a defensive parse of `realm_access` for a day after `ADR-0017` deleted
the code it pointed at. A guard that cannot fail is the thing it guards against, one level up.

Two properties, and they fail differently:

**The anchor exists.** Otherwise the mutation is a claim about deleted or moved code. The harness
says so itself — *"a mutation whose anchor has moved protects nothing"* — and the repository has
re-anchored by hand at least nine times, every time after noticing by accident.

**The anchor is unique.** The harness's own module docstring states this rule (*"the anchor text
must be unique in the file"*) and **never checked it**, which is how three of them came to match
two or three places. It edits the first match, so an ambiguous anchor silently reports on whichever
copy comes first in the file: `C2` claimed to defend the "no such row" branch of the model catalog
and was in fact editing the "un-lookupable name" branch three lines above it, a different property
with a different test. That is worse than a stale anchor, because it produces a confident `caught`.

Both are cheap to check and neither needs the source to be mutated, which is the whole point:
the expensive run proves the tests notice; this proves the expensive run is asking about this
codebase rather than about a previous one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _mutations() -> list[Any]:
    """The harness's own list, imported rather than parsed.

    Imported, unlike `test_capability_vocabulary`'s parsing of TypeScript, because this one *can*
    be: it is Python in this repository, and reading the objects rather than a regex over their
    source means a mutation written in any spelling the language allows is still checked. The
    concatenated string literals several anchors use are exactly that case — a regex over the file
    would see two fragments and look for neither.
    """
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.find_spec("mutation_check")
    assert spec is not None, "tools/mutation_check.py is not importable"
    module = importlib.import_module("mutation_check")
    mutations: list[Any] = list(module.MUTATIONS)
    return mutations


def test_there_are_mutations_to_check() -> None:
    """The guard's own failure mode, and this repository has shipped two guards that could not
    fail — both silently green, both found only by breaking something on purpose. An import that
    returned an empty list would make every assertion below pass vacuously."""
    assert len(_mutations()) > 100


def test_every_anchor_still_exists_in_the_file_it_names() -> None:
    missing = [
        f"{mutation.ident}  {mutation.path}\n      looked for: {mutation.old.splitlines()[0]!r}"
        for mutation in _mutations()
        if mutation.old not in (ROOT / mutation.path).read_text()
    ]

    assert not missing, (
        "these properties are claimed to be guarded and point at code that is not there:\n  "
        + "\n  ".join(missing)
        + "\n\nRe-anchor onto whatever carries the property now, or delete the entry if the rule "
        "it guarded is gone. Leaving it is the worse option of the two: it still reads as a claim "
        "that something is defended, and `make mutants` reports it hours later, if at all."
    )


def test_every_anchor_names_exactly_one_place() -> None:
    """The rule the harness's docstring states and its code never enforced.

    `mutation_check` applies `str.replace(old, new, 1)`, so an anchor matching several places edits
    the first one. The result is not a failure — it is a `caught` about a property nobody asked
    about, while the property named in the entry goes undefended.
    """
    ambiguous = [
        f"{mutation.ident}  {mutation.path}  matches {count} places"
        for mutation in _mutations()
        for count in [(ROOT / mutation.path).read_text().count(mutation.old)]
        if count > 1
    ]

    assert not ambiguous, (
        "these anchors match more than one place, and the harness edits the first:\n  "
        + "\n  ".join(ambiguous)
        + "\n\nWiden the anchor to something only the intended site carries — or, better, remove "
        "the duplication in the source: an anchor that matches three times usually means the same "
        "rule is written out three times, which is the defect one level down."
    )


def test_every_mutation_changes_something() -> None:
    """An entry whose replacement equals its anchor edits nothing, so the suite passes and the
    harness reports `SURVIVED` — a property read as undefended when nothing was ever broken. The
    same false reading as a stale anchor, arriving from the other direction."""
    inert = [mutation.ident for mutation in _mutations() if mutation.old == mutation.new]

    assert not inert, f"these mutations replace their anchor with itself: {inert}"


def test_every_test_selection_names_files_that_exist() -> None:
    """A selection naming a deleted file is a mutation checked against a narrower suite than it
    claims — and `pytest` treats a missing path as an error, so the harness would read the run as
    *failed*, which it scores as `caught`. A renamed test file would therefore turn every mutation
    pointing at it green at once."""
    missing = [
        f"{mutation.ident} -> {selection}"
        for mutation in _mutations()
        for selection in mutation.tests.split()
        if not (ROOT / selection).exists()
    ]

    assert not missing, (
        "these mutations run a test selection that does not exist:\n  "
        + "\n  ".join(missing)
        + "\n\nA missing path makes pytest exit non-zero, which the harness reads as the mutation "
        "having been caught. Every entry pointing at it would report green without a single test "
        "having run."
    )
