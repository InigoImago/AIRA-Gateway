"""The console reserves the scrollbar's space, whether or not there is a scrollbar.

A page that fits the viewport has no scrollbar. The moment anything grows it — a row opened in the
register, a banner, an info panel — one appears, the document loses the scrollbar's width, and
**every percentage width on the page is recomputed against a narrower figure**. Columns move.
Closing the thing moves them back. It is the jiggle nobody can point at, and it is the second cause
of the register being reported as unstable; `table-layout: fixed` fixes the first and cannot touch
this one, because a fixed column of 26% of a narrower table is a narrower column.

`InfoHint`'s own docstring records the worse version of the same thing, found in the model editor:
there the reflow slid the "i" out from under the pointer, which closed the panel, which removed the
scrollbar, which put the "i" back — a flicker loop that never settled. That was fixed by taking the
panel out of the document's scroll extent. This fixes the rest of the console by never changing the
extent's width in the first place.

## Why this is a file scan and not a browser test

It was written as a browser test first, and the browser could not see it. Headless Chromium draws
**overlay** scrollbars: `document.documentElement.clientWidth` measured 1280 on the register at
viewport heights of 400, 3000, 6000 and 9000 — scrolling and not scrolling alike. There is no width
to lose, so there is nothing for an assertion to catch. The defect is real in the browsers the
console is actually read in and invisible in the one the suite drives.

So this asserts the declaration rather than its effect, and says so. That is a weaker guard than
this project likes, and it is the strongest one available here: the alternative was a comment in a
stylesheet, which is the thing `LESSONS.md` calls a written-down danger rather than a guard.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STYLESHEET = ROOT / "management/frontend/src/styles.scss"

#: An `html { … }` block at the top level of the stylesheet. Deliberately anchored to the start of
#: a line: `html` also appears inside selectors like `html.dark`, and the gutter has to be on the
#: root element unconditionally to be reserved unconditionally.
ROOT_BLOCK = re.compile(r"^html\s*\{([^}]*)\}", re.M)


def test_the_stylesheet_this_checks_is_where_it_is_expected() -> None:
    """A moved or renamed stylesheet would turn the assertion below into a green nothing."""
    assert STYLESHEET.is_file(), STYLESHEET
    assert "--aira-" in STYLESHEET.read_text(encoding="utf-8"), (
        f"{STYLESHEET} does not look like the console's stylesheet"
    )


def test_the_document_reserves_the_scrollbar_gutter() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    blocks = [match.group(1) for match in ROOT_BLOCK.finditer(css)]

    assert blocks, (
        "no top-level `html { … }` block in the console stylesheet — the scrollbar gutter has "
        "nowhere to be reserved, and every page that can grow past the viewport will reflow "
        "narrower the moment it does"
    )
    assert any("scrollbar-gutter: stable" in block for block in blocks), (
        "`html { scrollbar-gutter: stable }` is gone. A page that grows past the viewport now "
        "gains a scrollbar, loses ~15px of width, and re-lays out every percentage width on it — "
        "which is what 'the elements jiggle when I open a row' means. Headless Chromium uses "
        "overlay scrollbars and will not notice; a reader on a desktop browser will."
    )
