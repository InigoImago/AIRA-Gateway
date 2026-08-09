"""Every relative link in the documentation points at something that exists.

A dead link in a page a newcomer is told to follow is worse than a missing page: it tells them the
documentation is not maintained, and they stop trusting the parts that *are* right. Checked here
rather than by a reviewer, because a reviewer checks the links in the diff and these break from a
file being moved somewhere else entirely.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = [
    *(ROOT / "docs").rglob("*.md"),
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CLAUDE.md",
]

#: `[text](target)` — markdown links only. Bare URLs and image sources are handled below.
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
IMAGE_SRC = re.compile(r'<img[^>]+src="([^"]+)"')


def _targets(text: str) -> list[str]:
    return [*LINK.findall(text), *IMAGE_SRC.findall(text)]


def test_the_scan_finds_the_pages_it_claims_to_check() -> None:
    """Without this the assertion below passes by describing nothing."""
    assert len(PAGES) > 15, f"only found {len(PAGES)} documentation pages"


def test_no_documentation_link_points_at_a_missing_file() -> None:
    broken: list[str] = []
    for page in PAGES:
        for target in _targets(page.read_text()):
            # External links, anchors and mailto are somebody else's problem.
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path, _, _anchor = target.partition("#")
            if not path:
                continue
            resolved = (page.parent / path).resolve()
            if not resolved.exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")

    assert broken == [], f"links pointing at nothing: {broken}"
