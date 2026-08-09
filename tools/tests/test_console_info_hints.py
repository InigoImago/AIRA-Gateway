"""Every `<app-info-hint>` in the console actually says something.

`InfoHint` takes its explanation as **projected content**, not as a `text` attribute. Angular
silently ignores an unknown attribute on a component element — no error, no warning, no red test —
so `<app-info-hint label="…" text="…" />` compiles, renders an "i", opens on hover, and shows an
**empty panel**.

Which is precisely the defect the component was created to prevent. `FRD-206` shipped info buttons
as `title` attributes that displayed nothing and the component was the fix; on 2026-08-09 three new
hints were written with `text=` and produced the same nothing, reported from the running console:
*"du fügst jetzt auch die info hover buttons überall, aber füllst sie nicht mit informationen"*.

**Why this lives in the Python suite.** The first version was an Angular spec using
`import.meta.glob` to read the templates. It did not work — the specs run in a browser environment,
the glob is unavailable at runtime, and the file failed to *load*: Vitest reported "0 tests" for it
while the run's total still read green. A guard that cannot fail is the thing it is guarding
against, one level up. Found by breaking a template on purpose and watching nothing happen.

So: a file scan, in the suite that has a filesystem and already scans source for
`test_declared_dependencies.py` and `test_documented_counts.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "management/frontend/src/app").rglob("*.html"))

#: `<app-info-hint …` up to the first `>` or `/>`. Deliberately not an HTML parser: what is being
#: checked is the *source* a person wrote, and a parser would normalise away the very difference
#: between a self-closing tag and one with content.
HINT = re.compile(r"<app-info-hint([^>]*?)(/>|>)", re.S)


def test_the_scan_finds_the_templates_it_claims_to_check() -> None:
    """Without this the assertions below pass by describing nothing — the failure mode of every
    check that sweeps a directory, and the one that hid the broken first version of this test."""
    assert len(TEMPLATES) > 10, f"only found {len(TEMPLATES)} templates under src/app"


def test_no_hint_uses_a_text_attribute_the_component_does_not_have() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in TEMPLATES
        for match in HINT.finditer(path.read_text())
        if "text=" in match.group(1)
    ]

    assert offenders == [], (
        "the explanation goes between the tags, not in a `text` attribute — Angular ignores it "
        f"and the panel opens empty: {offenders}"
    )


def test_no_hint_opens_an_empty_panel() -> None:
    """A self-closing hint has no projected content, so the panel it opens is blank — a control
    that looks informative and informs nobody."""
    offenders = [
        f"{path.relative_to(ROOT)}: {match.group(0)[:70]}"
        for path in TEMPLATES
        for match in HINT.finditer(path.read_text())
        if match.group(2) == "/>"
    ]

    assert offenders == [], offenders


def test_every_hint_carries_an_accessible_label() -> None:
    """`label` is `input.required`, so a missing one is a compile error — but a hint labelled with
    an empty string compiles and leaves a button whose accessible name is "What does  mean?"."""
    offenders = [
        f"{path.relative_to(ROOT)}: {match.group(0)[:70]}"
        for path in TEMPLATES
        for match in HINT.finditer(path.read_text())
        if 'label=""' in match.group(1) or '[label]=""' in match.group(1)
    ]

    assert offenders == [], offenders
