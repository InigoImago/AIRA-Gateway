"""This repository is public: it says what AIRA does, not what the predecessor's system looks like.

AIRA serves a compatibility surface for a predecessor API, so that name is on the wire
(`/kira/api/external`) and unavoidable — a client migrates by changing a base URL, which is the
whole point of `FRD-107`. What is avoidable is everything *around* it, and it had accumulated
without anybody deciding to publish it:

- the predecessor's **product name**, in four documents;
- its **specification document** by filename and section structure, in forty-seven places;
- a **source file** of theirs, cited in a comment;
- and the sharpest of them, its **security posture**: that it disables TLS verification and ships
  `allow_origins=["*"]` with credentials. Those sentences existed to explain why *our* rule is
  different, which is a good reason to state the rule and no reason at all to name whose weakness
  it is. A system that is presumably still running does not need its weak spots described in a
  public repository by its successor.

Dates come out of the two places that are read as *current* — this file's guidance and each FRD's
header. A status of "Done (2026-08-06)" tells a reader when somebody last touched a document,
which is a question `git log` answers better and a delivery timeline nobody chose to publish. The
DEVLOG keeps its dates on purpose: it is a log, and a log without dates is a list.

The check is mechanical because the alternative is remembering. Every one of the forty-seven
citations was defensible where it stood.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Text files under version control. `.gitignore` is exempt and stays that way: the rule keeping
#: the predecessor's document out of this repository has to name it, and losing that protection to
#: avoid one mention of a filename would be the trade in the wrong direction.
EXEMPT = {".gitignore", "tools/tests/test_public_repository_hygiene.py"}

FORBIDDEN = {
    r"KIA[-_ ]?KIRA": "the predecessor's product name",
    r"kira_api\.md": "the predecessor's specification document, by filename",
    r"api_service\.py": "a source file of the predecessor's",
    r"predecessor (?:ships|sets) ": "the predecessor's configuration, described",
    r"predecessor's source": "reading the predecessor's source, stated",
}

#: Read as current, so a date in them is a claim about now.
DATED_DOCUMENTS = ("CLAUDE.md", "docs/LESSONS.md", "docs/features/README.md")
_ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        ROOT / f
        for f in out
        if f not in EXEMPT and f.endswith((".md", ".py", ".ts", ".html", ".toml", ".yml", ".yaml"))
    ]


def test_there_are_files_to_check() -> None:
    """A guard on the guard: an empty file list passes every assertion below by checking nothing,
    and this repository has shipped two guards that could not fail — both silently green."""
    files = _tracked()

    assert len(files) > 200, len(files)
    assert any(f.name == "CLAUDE.md" for f in files)


@pytest.mark.parametrize(("pattern", "what"), sorted(FORBIDDEN.items()))
def test_the_predecessors_internals_are_not_published(pattern: str, what: str) -> None:
    regex = re.compile(pattern, re.IGNORECASE)
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in _tracked()
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
        if regex.search(line)
    ]

    assert not hits, (
        f"{what} appears in a public repository:\n  " + "\n  ".join(hits) + "\n\n"
        "Say what AIRA does and why its rule is what it is; naming whose system it differs from "
        "adds nothing a reader needs. The compatibility surface keeps its name — that is on the "
        "wire — but the predecessor's documents, sources and settings are not ours to publish."
    )


@pytest.mark.parametrize("document", DATED_DOCUMENTS)
def test_the_documents_read_as_current_carry_no_dates(document: str) -> None:
    """`docs/DEVLOG.md` is deliberately absent from this list."""
    hits = [
        f"{document}:{n}: {line.strip()[:80]}"
        for n, line in enumerate((ROOT / document).read_text().splitlines(), 1)
        if _ISO_DATE.search(line)
    ]

    assert not hits, (
        "these are read as a statement about now, so a date in them is a delivery timeline:\n  "
        + "\n  ".join(hits)
        + "\n\nWhen something happened belongs in `docs/DEVLOG.md`, and `git log` answers it "
        "better than a header nobody remembers to update."
    )


def test_the_frd_headers_carry_no_dates() -> None:
    """The header is the single source of a feature's status (`tools/features_index.py`), so it is
    read as current by definition — and the generated index inherits whatever is in it."""
    hits = []
    for path in sorted((ROOT / "docs" / "features").glob("FRD-*.md")):
        header = path.read_text().partition("\n## ")[0]
        if _ISO_DATE.search(header):
            hits.append(path.name)

    assert not hits, (
        f"these FRD headers carry a date: {hits}. The body may say when something was measured; "
        "the header says what the feature's status *is*."
    )
