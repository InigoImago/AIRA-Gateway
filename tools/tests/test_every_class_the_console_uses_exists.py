"""A class name in a template has a rule behind it, or is listed here as deliberate.

**A misspelled class does not fail.** It renders. The element simply gets whatever the browser
would have given a bare `div`, and the page looks *nearly* right — which is why this is the one
front-end mistake that survives review, a test suite and a demo. Angular will not warn, `tsc` never
sees a template's `class` attribute, and there is no linter here that would.

Found by measuring rather than reading, and it was not one typo:

- `/requests` and `/pipeline-tests` opened with `<div class="page"><header class="page__head">`
  and an `<h2 class="page__title">`. **None of the three exists.** Every other page uses `.stack`,
  which gives its children `gap: 1rem`; these two got nothing, so the heading sat flush against the
  content below it — 0px, measured — and on the pipeline page the tab strip sat flush against the
  card as well.
- `.hint` twice, where the console's small muted explanation is `.field__hint`: two paragraphs
  rendered as ordinary body text.
- `.badge--ok` on the *Active* rate-limit badge, where the green one is `.badge--success`.
- `.table-scroll` twice on the connection panel, where the scroll container is `.table-wrap` — so
  those two tables had no scroll container at all.
- `.right` on a table cell holding a delete button, where the console's right-aligned actions cell
  is `.table__actions`.

Six names, four files, none of them noticed by anything. Hence a scan.

## What counts as "has a rule"

Any `.name` appearing in the global stylesheet, in a component `.scss`, or in an inline `styles:`
block. That is deliberately generous: this test is looking for names nothing *anywhere* styles, not
for names used in a way somebody might improve.

`ALLOWED` below is the honest remainder — names that are on an element for a reason other than
styling it. Each is listed with that reason. Adding to it is fine and is meant to be a decision
somebody writes down, which is the whole difference between this list and the state before it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "management/frontend/src"

#: Names carried for a reason other than style. Keep the reason with the name.
ALLOWED = {
    # Structural hooks in the shell, styled through their parent's element selectors.
    "aira-header__brand",
    "aira-nav__item",
    "aira-user__name",
    "panel",
    # A wrapper inside `.modal`, which carries the box; the head/body/foot inside carry the padding.
    "modal__panel",
    "modal__panel--wide",
    # Named for the reader of the template. The behaviour is already an element selector:
    # `input, select, textarea` styles the control, and `.table td` breaks and wraps.
    "input",
    "break-anywhere",
    "wrap",
    "code-block",
    "inline-number",
    "field__label",
    # `.field` is full-width in a window by rule now; the name records that this one wanted it
    # before that was true.
    "field--own-row",
    # A named part of a component's own composition, laid out by the `card stack` beside it.
    "pipe__test",
    "pager__count",
}

#: `class="…"` in a template. Bindings (`[class.x]`, `[ngClass]`) are out of scope: those names are
#: in TypeScript, where a reader at least sees them next to the logic that sets them.
CLASS_ATTR = re.compile(r'class="([^"{}]+)"')
#: HTML comments, stripped first — this file's own explanation names `.page__head`, and a scan that
#: reads its own documentation reports the thing it just described as still present.
COMMENT = re.compile(r"<!--.*?-->", re.S)
INLINE_STYLES = re.compile(r"styles\s*:\s*\[?\s*`(.*?)`", re.S)
SELECTOR = re.compile(r"\.([a-zA-Z][\w-]*)")


def _sources() -> list[Path]:
    app = SRC / "app"
    return sorted(
        list(app.rglob("*.html")) + [p for p in app.rglob("*.ts") if "spec" not in p.name]
    )


def _defined() -> set[str]:
    css = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.scss"))
    for path in SRC.rglob("*.ts"):
        if "spec" in path.name:
            continue
        for match in INLINE_STYLES.finditer(path.read_text(encoding="utf-8")):
            css += "\n" + match.group(1)
    return set(SELECTOR.findall(css))


def _used() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for path in _sources():
        text = COMMENT.sub("", path.read_text(encoding="utf-8"))
        for match in CLASS_ATTR.finditer(text):
            for name in match.group(1).split():
                used.setdefault(name, set()).add(path.relative_to(SRC).as_posix())
    return used


def test_the_scan_finds_the_templates_and_the_stylesheet() -> None:
    """Neither half may be empty, or the assertion below passes by describing nothing."""
    assert len(_sources()) > 30, len(_sources())
    defined = _defined()
    assert {"stack", "card", "field", "table-wrap"} <= defined, sorted(defined)[:20]


def test_no_class_in_a_template_is_styled_by_nothing() -> None:
    defined = _defined()
    orphans = {
        name: where
        for name, where in _used().items()
        if name not in defined and name not in ALLOWED
    }

    assert orphans == {}, (
        "these class names have no rule in any stylesheet, so the element gets whatever the "
        "browser gives a bare tag — which looks nearly right and is how `.page__head` left two "
        "pages with no gap under their heading:\n  "
        + "\n  ".join(f".{n:<24} {', '.join(sorted(w))}" for n, w in sorted(orphans.items()))
        + "\n\nEither use the name the stylesheet already has, write the rule, or add the name to "
        "ALLOWED with the reason it is on the element."
    )


def test_the_allow_list_does_not_outlive_its_entries() -> None:
    """An entry that is no longer used anywhere is a name somebody removed and a reason nobody did.

    Without this the list only grows, and a reader cannot tell which entries still describe the
    console — the same rot that put twenty-two FRD headers at *Draft*.
    """
    used = set(_used())
    stale = sorted(ALLOWED - used)

    assert stale == [], (
        f"ALLOWED still excuses names no template uses: {stale}. Delete them — the exemption "
        "outlived the markup it was written for."
    )
