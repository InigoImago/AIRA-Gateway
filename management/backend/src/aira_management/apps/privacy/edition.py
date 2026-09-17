"""Which edition of the notice this is, and the digest that keeps the date honest (`FRD-625` FR-7).

A notice carries the date it was last changed ("Stand"). A date only a person moves is one that
stays put while the text changes under it, so ``SOURCE_DIGEST`` pins the sources the notice is made
of — the register and every language file — and `test_privacy_notice.py` fails when they no longer
hash to it. The failure names the new digest; moving ``EDITION`` to the day of the change is the
other half of the same edit.

A new edition is also a new `notice.version`, so everybody is asked to read it again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The day the notice's content last changed, ISO 8601.
EDITION = "2026-09-17"

#: `source_digest()` as of ``EDITION``.
SOURCE_DIGEST = "7b76a7449935558770547c08cec47a57ff45c06d587adc83c33e324e4a2bbe05"

#: What the notice is made of. The loader and the renderer decide how it reads, not what it says.
SOURCES: tuple[Path, ...] = (HERE / "activities.py", *sorted((HERE / "texts").glob("*.toml")))


def source_digest() -> str:
    """SHA-256 over every source, by name and content, in a fixed order."""
    digest = hashlib.sha256()
    for path in SOURCES:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()
