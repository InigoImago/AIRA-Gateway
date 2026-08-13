"""`CLAUDE.md` carries conventions, not a feature log.

It had grown to 1801 lines, of which **§6 "Current status" was 1667 — 93% of the file** and, read
side by side with `docs/DEVLOG.md`, a third copy of it: the same rounds, the same measurements, in
the same prose. The length was the complaint; it was not the defect.

**The defect was that feature status lived in two places.** Each FRD carries a `Status:` header,
and §6 restated it. On 2026-08-13 twenty-two of them disagreed — `FRD-100`, the surface every
request goes through, still said *Draft*, as did tool calling, all of Phase 0 and most of Phase 2.
The copy that is loaded every session stayed true; the copy nobody opens rotted. That is *a
hand-written list with no counterpart*, this repository's most repeated shape, arriving in the
documentation instead of in the code.

So the headers are the source, `docs/features/README.md` is generated from them, the narrative
lives in the DEVLOG and the accumulated rules in `docs/LESSONS.md` — and this keeps §6 from
growing back one well-intentioned paragraph at a time. Each paragraph is defensible on its own;
that is exactly how 1667 lines happen.

Raising the limit is a legitimate change. Doing it without moving anything into the documents that
exist for it is not.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / "CLAUDE.md"

#: Generous on purpose: about twice what §6 needs today, so ordinary editing never trips it and a
#: return of the feature log cannot fit under it.
STATUS_LIMIT = 90
FILE_LIMIT = 260

_SECTION = re.compile(r"^## \d+\. ", re.MULTILINE)


def _status_section() -> list[str]:
    text = CLAUDE.read_text()
    start = text.index("## 6. Current status")
    rest = _SECTION.search(text, start + 1)
    return text[start : rest.start() if rest else len(text)].splitlines()


def test_the_section_is_found_at_all() -> None:
    """A guard on the guard: if §6 is renamed, this file must move with it rather than passing by
    measuring nothing."""
    assert len(_status_section()) > 5


def test_the_status_section_stays_a_summary() -> None:
    lines = _status_section()

    assert len(lines) <= STATUS_LIMIT, (
        f"§6 is {len(lines)} lines (limit {STATUS_LIMIT}). Per-feature status belongs in the FRD's "
        "own header (`docs/features/README.md` is generated from it), the story of a round belongs "
        "in `docs/DEVLOG.md`, and a rule that generalises belongs in `docs/LESSONS.md`. §6 stood "
        "at 1667 lines once, one defensible paragraph at a time."
    )


def test_the_whole_file_stays_readable() -> None:
    lines = CLAUDE.read_text().splitlines()

    assert len(lines) <= FILE_LIMIT, (
        f"CLAUDE.md is {len(lines)} lines (limit {FILE_LIMIT}). It is loaded in full at the start "
        "of every session, so what is in it is paid for every time — it holds conventions, and "
        "everything else has a document of its own."
    )


def test_the_status_section_points_at_the_documents_that_hold_the_detail() -> None:
    """The section is only short *because* the detail is elsewhere. A link that goes missing turns
    a summary into an omission."""
    section = "\n".join(_status_section())

    for target in ("docs/features/README.md", "docs/DEVLOG.md", "docs/LESSONS.md", "docs/adr/"):
        assert target in section, f"§6 no longer points at {target}"


def test_the_documents_it_points_at_exist() -> None:
    for target in ("docs/features/README.md", "docs/DEVLOG.md", "docs/LESSONS.md"):
        assert (ROOT / target).is_file(), f"{target} is linked from CLAUDE.md and does not exist"
