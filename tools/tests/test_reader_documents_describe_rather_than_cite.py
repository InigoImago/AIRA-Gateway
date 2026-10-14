"""A document written for a human reader describes the system; it does not cite the development
record.

The owner's rule: nobody reading the human documentation cares about a link to an FRD or an ADR.
An operator configuring Kafka needs the requirement, not the number of the feature document an agent
wrote it down in; a link to the decision record makes the page incomplete without a second document
the reader never meant to open. The FRDs, the ADRs, the development log, the lessons and `CLAUDE.md`
are how this project is built — `CONTRIBUTING.md` points at them, and the planning documents (PRD,
ROADMAP, gap analysis) are maps onto them. The pages below are what the system *is*.

The rule was stated before and not kept: these pages carried 377 citations, and a round of
documentation fixes raised that to 665, each defensible on its own. A rule only a reviewer enforces
is one the next round breaks.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The documents written for somebody who runs, integrates, operates or calls the system.
READER_DOCUMENTS = sorted(
    {
        ROOT / "README.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "REQUEST-LIFECYCLE.md",
        ROOT / "docs" / "CONFIGURATION.md",
        ROOT / "docs" / "INTEGRATIONS.md",
        ROOT / "docs" / "DEPLOYMENT.md",
        ROOT / "docs" / "SETUP.md",
        ROOT / "docs" / "ROLES.md",
        ROOT / "docs" / "API.md",
        ROOT / "docs" / "TESTING.md",
        ROOT / "docs" / "MIGRATION-KIRA.md",
        ROOT / "docs" / "MIGRATION-GEMINI.md",
        *(ROOT / "docs" / "deployment").glob("*.md"),
        ROOT / "docs" / "measurements" / "README.md",
        ROOT / "config" / "README.md",
        ROOT / "deploy" / "compose" / "README.md",
        *(ROOT / "deploy" / "compose" / "otel").glob("*/README.md"),
        ROOT / "e2e" / "README.md",
        ROOT / "tests" / "integration" / "README.md",
        ROOT / "tools" / "loadtest" / "README.md",
        ROOT / "tools" / "opencode" / "README.md",
        ROOT / "management" / "frontend" / "README.md",
    }
)

#: What points into the development record: a feature or decision document by number or by path,
#: the log, the lessons, the agent's own guidance file.
_CITATION = re.compile(
    r"\bFRD-\d{3}|\bADR-\d{4}|features/FRD|adr/ADR|features/README|adr/README"
    r"|\bDEVLOG\b|\bLESSONS\b|CLAUDE\.md"
)


def test_every_reader_document_is_scanned() -> None:
    """A guard on the guard: a moved page would drop out of the scan silently, and an absence check
    over fewer pages passes more easily, never less."""
    missing = [str(page.relative_to(ROOT)) for page in READER_DOCUMENTS if not page.exists()]
    assert not missing, f"listed as reader documents but not there: {missing}"
    assert len(READER_DOCUMENTS) >= 25


def test_a_reader_document_cites_nothing_from_the_development_record() -> None:
    citations = [
        f"{page.relative_to(ROOT)}:{number}: {match.group(0)}"
        for page in READER_DOCUMENTS
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1)
        for match in _CITATION.finditer(line)
    ]
    assert not citations, (
        "A reader-facing document cites the development record. Say what the system does in the "
        "sentence instead — open the FRD or ADR, take the fact the sentence needs, and write "
        "it:\n  " + "\n  ".join(citations[:30])
    )
