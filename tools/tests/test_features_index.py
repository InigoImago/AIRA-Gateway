"""The FRD index says what the FRD headers say — in both directions.

Feature status used to live twice: in each FRD's own header, and in a prose section of `CLAUDE.md`
that grew to 1667 lines. On 2026-08-13 the two disagreed about **twenty-two** features. `FRD-100`
— the Gemini surface every request goes through — still said *Draft*, as did tool calling, all of
Phase 0 and most of Phase 2. The copy read every session stayed true and the copy nobody opens
rotted: this project's oldest defect shape (*a hand-written list with no counterpart*) arriving in
the documentation rather than in the code.

The header is the source now, `docs/features/README.md` is generated from it, and this is the
counterpart. Both directions matter and they fail differently:

    a status changed and the index not regenerated   → the index understates or overstates
    an FRD added and never indexed                   → a feature nobody browsing can find

Neither announces itself, which is why the check is mechanical rather than a review habit.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from features_index import INDEX, read, render  # noqa: E402


def test_the_reader_finds_the_features_it_is_built_around() -> None:
    """A guard on the guard. A parser that matches nothing makes every assertion below pass by
    comparing an empty index to an empty one — this repository has shipped two guards that could
    not fail, both silently green."""
    features = read()

    assert len(features) > 50, [f.number for f in features]
    assert any(f.number == "100" for f in features), "the Gemini surface is missing"


def test_the_committed_index_is_what_the_headers_say() -> None:
    """Run `uv run python tools/features_index.py --write` when this fails."""
    assert INDEX.read_text() == render(read()), (
        "docs/features/README.md no longer matches the FRD headers. Regenerate it with "
        "`uv run python tools/features_index.py --write` — and if the diff surprises you, it is "
        "the headers that are the source, not the table."
    )


def test_every_frd_carries_a_status_a_reader_can_find() -> None:
    """`read()` raises on a header it cannot parse, so this is the case that motivated it:
    `FRD-406` used a bullet list of its own and was invisible to any tool reading the headers —
    including this one, which is exactly how a second format survives."""
    for feature in read():
        assert feature.status, feature.path
        assert feature.title, feature.path


def test_a_draft_is_not_read_as_delivered() -> None:
    """`FRD-121` is *"Draft — optional, and probably should not be built first"*. A keyword search
    for "built" would count it as shipped, which is the one direction that matters: overstating
    what exists is how a reader concludes a control is in place."""
    drafts = {f.number: f for f in read() if not f.delivered}

    assert "121" in drafts, "a draft that says 'should not be built' is being counted as built"
    assert not drafts["121"].delivered
