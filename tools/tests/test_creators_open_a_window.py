"""Everything this console creates, it creates in a window.

Reported from the console: *"Issue key in the use-case overview is not in a window, not consistent
with the other elements in the UI."* It was the last inline creator — a form that unfolded inside
the panel, while budgets, rate limits, anomaly rules, global rules and model declarations all open
a window. The reader learns the pattern four times and meets an exception on the fifth, which is
worse than either pattern used consistently.

`core/ui/modal.ts` says why a window rather than an unfolding form, and it is not decoration: an
inline form scrolls the page to a control far from the row it is about, leaves the list behind it
clickable, and says nothing about what it is editing — `FRD-206` recorded a second *Edit* silently
replacing the first one's unsaved changes for exactly that reason.

**Asserted on the behaviour, not on the component.** The model catalog hand-rolled two windows
before `app-modal` existed, and they are windows: `role="dialog"`, a backdrop, their own Escape.
Requiring the shared component would fail them for being early rather than for being wrong, and
would say nothing about a sixth screen that hand-rolls a third one correctly.

Nothing here can tell a *good* window from a bad one. What it can tell is a creator that opens no
window at all, which is the reported defect and the one that repeats.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "management" / "frontend" / "src" / "app" / "features"

#: A control that opens a form for making something new.
#:
#: Matched on the **label** as well as the testid, and the label is the half that matters: the
#: reported control had no testid at all, so a guard reading only testids would have gone green
#: over precisely the case it exists for. `+ Something` is this console's convention for a creator,
#: and it is what a reader recognises too.
_CREATOR = re.compile(r'data-testid="((?:add|issue|new)-[a-z0-9-]+)"|(\+ (?:Add|New|Issue) [a-z]+)')

#: What counts as a window. The shared control, or a hand-rolled one that is still a dialog.
_WINDOW = re.compile(r"<app-modal|role=\"dialog\"")

#: Creator-shaped controls that open no form. Named with the reason, because a silent skip list is
#: how a real creator comes to be exempt by accident.
NOT_CREATORS = {
    # Runs the reachability check on the model being declared — a verb on the open form, not a
    # second form (`FRD-506`).
    "issue-check",
    # Appends an empty **row to a list that is already being edited in place**, inside the pipeline
    # builder's step configuration. There is no form to open: the row appears among its siblings,
    # which is where somebody adding a routing category is looking. A window here would take the
    # reader away from the table they are filling in — the opposite of what one is for.
    "+ Add category",
}


def _templates() -> list[Path]:
    return sorted(FEATURES.rglob("*.html"))


def test_there_are_creators_to_check() -> None:
    """A guard on the guard: a pattern that matches nothing passes the assertion below by checking
    nothing, and this repository has shipped two guards that could not fail."""
    found = {
        match
        for path in _templates()
        for pair in _CREATOR.findall(path.read_text())
        for match in pair
        if match
    } - NOT_CREATORS

    assert len(found) >= 6, sorted(found)
    # By label, deliberately: the control this guard was written for carried no testid.
    assert "+ Issue key" in found, "the reported control is no longer matched by this guard"


def test_every_creator_opens_a_window() -> None:
    inline: list[str] = []
    for path in _templates():
        source = path.read_text()
        creators = {m for pair in _CREATOR.findall(source) for m in pair if m} - NOT_CREATORS
        if creators and not _WINDOW.search(source):
            inline.append(f"{path.relative_to(ROOT)}: {sorted(creators)}")

    assert not inline, (
        "these screens open a form that is not a window:\n  "
        + "\n  ".join(inline)
        + "\n\nEvery other creator in this console opens one, so an inline form is the exception a "
        "reader meets after learning the pattern. `core/ui/modal.ts` owns the three promises a "
        "hand-rolled panel forgets one of — Escape closes, the keyboard moves in, the backdrop "
        "closes."
    )
