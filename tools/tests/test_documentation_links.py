"""Every relative link in the documentation points at something that exists — and so does every
command it tells the reader to run.

A dead link in a page a newcomer is told to follow is worse than a missing page: it tells them the
documentation is not maintained, and they stop trusting the parts that *are* right. Checked here
rather than by a reviewer, because a reviewer checks the links in the diff and these break from a
file being moved somewhere else entirely.

**A `make` target is a link too, and it breaks the same way.** `docs/deployment/showcase.md` — the
page written for somebody doing a first run — told the reader to finish with `make
down-full-volumes`, a target that has never existed. `make` answers *"No rule to make target"*,
which reads as a broken repository rather than as a typo in a document, and it does so at the last
step, after twenty minutes of setting the demo up. This repository has a name for the shape: an
instruction with no destination (`FRD-208`), and it is the harder half of `FRD-206`'s complaint,
because a control that does not exist announces itself through nothing at all.
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


#: A fenced block, and an inline `code` span. Commands are read **only** out of these.
#:
#: The first version scanned the prose too and needed a list of English words that may follow
#: "make" — "make sure", "make sense", "make us", "make three of them". That list is a hand-written
#: list with no counterpart, which is the very defect this file is an instance of: it would have
#: grown by one word per false positive until somebody deleted the check.
#:
#: Formatting is the honest signal instead. A reader runs what is typeset as a command, and an
#: author who writes `` `make down` `` is making a claim about a target in a way that "we should
#: make this safe" is not. It also fails in the right direction — a dead instruction written as
#: plain prose is missed, and a paragraph is never flagged.
CODE_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]+`")

#: `make <target>` inside one of those. The target is the first word after `make`; anything after
#: it is an argument (`make test-e2e ARGS=…`) or a shell continuation.
MAKE_INVOCATION = re.compile(r"(?:^|[\s;&|(])make\s+([a-zA-Z0-9_-]+)")


def _commands(text: str) -> list[str]:
    return [*CODE_BLOCK.findall(text), *INLINE_CODE.findall(text)]


def _make_targets() -> set[str]:
    """Every target the Makefile defines, including the `.PHONY` declarations."""
    source = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-zA-Z0-9_-]+):", source, re.M))


def test_the_makefile_defines_targets_this_check_can_compare_against() -> None:
    """The guard's own footing: an empty target set would make every instruction below look dead,
    and an unparsed Makefile would make every one look fine, depending on which way it failed."""
    targets = _make_targets()

    assert {"up", "down", "test", "showcase"} <= targets, sorted(targets)


def test_the_scan_reads_commands_out_of_the_pages() -> None:
    """The other footing, and the one that would fail silently. If the fence pattern stopped
    matching, the check below would find no commands anywhere and pass — reporting that every
    documented instruction is sound because it read none of them."""
    found = {
        name
        for page in PAGES
        for block in _commands(page.read_text())
        for name in MAKE_INVOCATION.findall(block)
    }

    assert {"up", "down", "showcase"} <= found, sorted(found)


def test_every_make_command_the_documentation_gives_is_a_real_target() -> None:
    targets = _make_targets()
    dead: list[str] = []
    for page in PAGES:
        for block in _commands(page.read_text()):
            for name in MAKE_INVOCATION.findall(block):
                if name not in targets:
                    dead.append(f"{page.relative_to(ROOT)} -> make {name}")

    assert dead == [], (
        "the documentation tells the reader to run targets that do not exist:\n  "
        + "\n  ".join(dead)
        + "\n\n`make` answers 'No rule to make target', which reads as a broken repository rather "
        "than as a typo — and these sit at the end of setup guides, after the long part."
    )
