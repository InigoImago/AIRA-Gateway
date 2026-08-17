"""No source file may look like a binary to the tools everyone searches with.

Found on 2026-08-17, in the middle of an audit whose whole method was searching the source.
`model-release-panel.ts` compared two lists with ``join('\\0')`` written as **raw NUL bytes**
rather than as the escape. That is valid TypeScript and the same string to the compiler — and it
makes grep classify the file as binary and skip it **silently**, with no message and exit status 1.

The consequence was not theoretical. `grep -rn allowed_models` across the console reported that
nothing writes it, so *"which models a use case may call cannot be set in the console"* was about
to be reported as a finding, on the day somebody had asked for exactly that audit. The one file
that answers the question was the one file the search could not see.

A silent skip is the worst failure mode a search tool has: an empty result and a true search are
the same output. So the rule is not "avoid NUL" — it is that a tracked text file must be readable
as text, and any byte that trips the standard heuristic is a byte that belongs in an escape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Suffixes that are source, and are searched by hand and by every guard in `tools/tests`.
TEXT_SUFFIXES = (".md", ".py", ".ts", ".html", ".scss", ".css", ".toml", ".yml", ".yaml", ".json")

#: Bytes that make a file "binary" to the common heuristic. NUL is the one that has happened; the
#: other C0 controls are here because they are equally invisible in a diff and equally legal in a
#: string literal, and the fix for all of them is the same three characters.
FORBIDDEN = {0x00: "NUL", 0x01: "SOH", 0x02: "STX"}


def _tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [ROOT / name for name in listed if name.endswith(TEXT_SUFFIXES)]


def test_the_file_list_is_not_empty() -> None:
    """A guard on the guard: an empty list passes the assertion below by checking nothing."""
    files = _tracked_text_files()

    assert len(files) > 200, len(files)


def test_no_source_file_carries_a_byte_that_makes_it_binary() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        raw = path.read_bytes()
        for byte, name in FORBIDDEN.items():
            if bytes([byte]) in raw:
                offenders.append(f"{path.relative_to(ROOT)}: contains {name} (0x{byte:02X})")

    assert not offenders, (
        "These tracked source files contain a control byte that makes standard text tools treat "
        "them as binary and skip them **without saying so**:\n  " + "\n  ".join(offenders) + "\n\n"
        "Write the byte as an escape (`'\\0'`), which is the same string to the compiler and a "
        "text file to grep, every code review tool, and every guard in tools/tests."
    )
