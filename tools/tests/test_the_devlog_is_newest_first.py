"""`docs/DEVLOG.md` keeps the order its first line promises: newest entries on top.

Thirty-one entries, written between 2026-08-08 and 2026-09-18, sat **below** the oldest entry of
all, oldest first, while every other entry was newest first. Each had been appended where a merge
left it. A reader going back in time from the top met the project kickoff and stopped there, and
the round that built the permission engine was a thousand lines past the end of the history it
belonged to. Nothing said so: the file is long, and an entry in the wrong place reads exactly like
one in the right place.

An entry whose heading carries no date belongs to the date of the entry above it — the log's
convention for several entries written on one day.
"""

from __future__ import annotations

import re
from pathlib import Path

DEVLOG = Path(__file__).resolve().parents[2] / "docs" / "DEVLOG.md"

_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _dated_headings() -> list[tuple[str, str]]:
    """``(date, heading)`` per entry, in file order; an undated heading inherits the one above."""
    dated: list[tuple[str, str]] = []
    current = ""
    for heading in _HEADING.findall(DEVLOG.read_text(encoding="utf-8")):
        found = _DATE.search(heading)
        current = found.group(0) if found else current
        dated.append((current, heading))
    return dated


def test_the_log_has_dated_entries_to_order() -> None:
    """A guard on the guard: a heading format this does not parse would leave nothing to compare,
    and an order check over nothing passes."""
    dated = _dated_headings()
    assert len(dated) > 100
    assert all(date for date, _ in dated), "an entry above the first dated one has no date"


def test_every_entry_is_older_than_or_as_old_as_the_one_above_it() -> None:
    dated = _dated_headings()
    misplaced = [
        f"{heading!r} ({date}) sits below {above!r} ({above_date})"
        for (above_date, above), (date, heading) in zip(dated, dated[1:], strict=False)
        if date > above_date
    ]
    assert not misplaced, (
        "docs/DEVLOG.md promises its newest entries on top. Move these entries up to their "
        "date:\n  " + "\n  ".join(misplaced[:10])
    )
