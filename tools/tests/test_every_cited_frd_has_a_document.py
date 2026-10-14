"""Every FRD number the documentation cites has a document — or is listed below with the reason it
has none.

**Why this exists.** The planning documents cited `FRD-600` as "the governance view" for two months
after that view was built as `FRD-608`; `FRD-600` was never written, so a planner following the
reference found nothing and concluded the feature was missing. The ROADMAP named `FRD-700`,
`FRD-701` and `FRD-702` as Phase 7's documents; none exists. `FRD-129` was cited by two FRDs and two
source files as if it were a document; it was the label of a live test round. And `FRD-127` was
first a label for a fix with no document, then the number of a different feature, so the two
FRDs that cite the fix point a reader at the wrong one.

A number without a document is not always a mistake — a withdrawn feature is still cited where the
withdrawal matters. So those are listed here, each with its reason, and the list is checked in the
other direction too: a number that gets its document leaves the list, or the list starts lying.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "docs" / "features"

#: Pages whose citations a reader follows. The development log and the lessons are history and cite
#: numbers as they stood on the day.
PAGES = sorted(
    {
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CLAUDE.md",
        *(ROOT / "docs").rglob("*.md"),
    }
    - {ROOT / "docs" / "DEVLOG.md", ROOT / "docs" / "LESSONS.md"}
)

#: Numbers cited without a document of their own, and why that is correct.
WITHOUT_A_DOCUMENT = {
    "106": "the OpenAI-compatible inbound surface, withdrawn on 2026-08-07 before it was written",
    "129": "the label of a live test round of the coding-assistant work, never a document",
    "301": "routing, planned as its own FRD and specified inside FRD-300 instead",
    "600": "the governance views, planned and never written; the view was built as FRD-608",
    "700": "Phase 7 hardening, planned and never written",
    "701": "Kubernetes and Helm, planned and never written",
    "702": "performance and HA, never written; measured as FRD-136, HA is FRD-127",
}

_CITATION = re.compile(r"\bFRD-(\d{3})")


def _documented() -> set[str]:
    return {path.name[4:7] for path in FEATURES.glob("FRD-[0-9][0-9][0-9]-*.md")}


def _citations() -> dict[str, list[str]]:
    cited: dict[str, list[str]] = {}
    for page in PAGES:
        for number in _CITATION.findall(page.read_text(encoding="utf-8")):
            cited.setdefault(number, []).append(str(page.relative_to(ROOT)))
    return cited


def test_the_scan_finds_documents_and_citations() -> None:
    """A guard on the guard: a glob or a pattern that found nothing would pass everything below."""
    assert len(_documented()) > 90
    assert len(_citations()) > 90


def test_every_cited_number_has_a_document_or_a_reason() -> None:
    missing = {
        number: sorted(set(pages))
        for number, pages in _citations().items()
        if number not in _documented() and number not in WITHOUT_A_DOCUMENT
    }
    assert not missing, (
        "These FRD numbers are cited and have no document in docs/features/: "
        f"{missing}. Cite the FRD that exists, or — if the number is right and the document "
        "deliberately absent — add it to WITHOUT_A_DOCUMENT with the reason."
    )


def test_a_number_listed_as_absent_really_has_no_document() -> None:
    """The other direction: an entry that got its document must leave the list, or the list says a
    feature has no specification while one is sitting in docs/features/."""
    written = sorted(set(WITHOUT_A_DOCUMENT) & _documented())
    assert not written, f"FRD {written} now have documents; remove them from WITHOUT_A_DOCUMENT"
