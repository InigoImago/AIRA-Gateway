"""The console labels money in `AIRA_CURRENCY`, never in a symbol of its own.

Eight labels said `$` — spend tiles, table headers, the model editor's price fields — on an
installation whose figures are in EUR, and a unit test could not see it because every one of them
was a string the test did not ask about. The UI audit's screenshots showed it (`make ui-audit`).
Units come from `unitSuffix` and `perMillion` in `core/api/me.service.ts`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "management" / "frontend" / "src" / "app"

#: `Spend ($)`, `Input $ / 1M`, `€ 12`, `(EUR)` — a unit written into a label.
SYMBOL = re.compile(r"\(\s*[$€£]\s*\)|[$€£]\s*/\s*1M|€|£|\((EUR|USD|CHF|GBP)\)")


#: A comment may quote a label as an example; it renders nothing.
COMMENT = re.compile(r"\s*(\*|//|/\*|<!--)")


def test_no_label_carries_a_currency_of_its_own() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in sorted(APP.rglob("*"))
        if path.suffix in {".ts", ".html"} and not path.name.endswith(".spec.ts")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if SYMBOL.search(line) and not COMMENT.match(line)
    ]
    assert not offenders, (
        "These labels write a currency themselves; use unitSuffix/perMillion from "
        "core/api/me.service.ts so they say AIRA_CURRENCY:\n" + "\n".join(offenders)
    )


def test_the_pattern_recognises_what_it_exists_for() -> None:
    for label in ("Spend ($)", "Input $ / 1M", "Spend (EUR)", "€ 12"):
        assert SYMBOL.search(label), label
    for text in ("/^\\d+([.,]\\d{1,6})?$/", "`${API}/v1/`", "Spend{{ unit() }}"):
        assert not SYMBOL.search(text), text
