"""The tab icon is the AIRA mark, and it is the *current* mark.

`favicon.ico` was Angular's default, byte for byte, from the Phase 0 shell until the owner noticed
it in a browser tab. Nothing else could have noticed: no test asserts what an image looks like, the
file is never fetched by the suite, and every page renders correctly with the wrong icon in the tab.
A framework's logo on every tab of a governance console is a small thing that says something untrue
about what the reader is looking at.

So the icon is **generated from the mark's own geometry** (`tools/make_favicon.py`) and this
regenerates and compares. That closes the failure the fix would otherwise have introduced: two
images of one logo, drifting the day somebody changes a colour in the SVG — this repository's most
repeated shape, and the `.ico` is exactly the copy nobody opens.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from make_favicon import ICON, MARK, SIZES, build  # noqa: E402

INDEX = ROOT / "management" / "frontend" / "src" / "index.html"


def test_the_committed_icon_is_what_the_mark_renders_to() -> None:
    """Run `uv run python tools/make_favicon.py --write` when this fails."""
    assert ICON.read_bytes() == build(), (
        "favicon.ico no longer matches aira-mark.svg. Regenerate it with "
        "`uv run python tools/make_favicon.py --write` — the SVG is the source, and an icon that "
        "keeps yesterday's mark is wrong in the one place nobody looks."
    )


def test_the_icon_carries_the_sizes_a_browser_asks_for() -> None:
    """A guard on the guard: the comparison above passes just as happily on an empty file."""
    data = ICON.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])

    assert (reserved, kind) == (0, 1), "not an ICO"
    assert count == len(SIZES), f"{count} images, expected {len(SIZES)}"
    assert len(data) > 500, len(data)


def test_the_icon_is_not_a_framework_default() -> None:
    """The specific thing that shipped. Angular's favicon is ~15 kB of legacy BMP-encoded sizes;
    the generated one is ~1 kB of PNG. Asserted on the trait rather than on a checksum, so that a
    future framework default is caught too."""
    assert len(ICON.read_bytes()) < 4096, (
        "favicon.ico is large enough to be a framework default rather than the generated mark"
    )


def test_the_page_asks_for_both_forms() -> None:
    """The SVG is what every current browser uses; Safari has never supported an SVG favicon, so
    the `.ico` is a deliberate fallback. Dropping either leaves one family of readers looking at
    whatever the browser invents."""
    html = INDEX.read_text()

    assert 'type="image/svg+xml" href="aira-mark.svg"' in html
    assert 'href="favicon.ico"' in html


def test_the_mark_the_console_shows_is_the_one_in_the_tab() -> None:
    """One mark, two places. The header `<img>` and the tab icon must not become two logos."""
    app = (ROOT / "management" / "frontend" / "src" / "app" / "app.html").read_text()

    assert 'src="aira-mark.svg"' in app
    assert MARK.is_file()
