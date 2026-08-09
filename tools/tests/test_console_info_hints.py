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


# ---- a control that starts a request must survive it -----------------------------------------


def _enclosing_blocks(source: str, position: int) -> list[str]:
    """The Angular control-flow blocks open at ``position``, outermost first.

    Brace counting rather than a template parser: what is being checked is a *structural* mistake
    in the source, and every parser here would have to be taught Angular's `@if`/`@else` syntax to
    find it.
    """
    open_blocks: list[str] = []
    depth_of: list[int] = []
    depth = 0
    index = 0
    #: The header of the block that most recently closed at each depth. An `@else` says nothing
    #: about *what* it is the alternative to, so it inherits the `@if` it belongs to — without
    #: that, this scanner misses the exact shape it exists to find, which is how its first version
    #: passed while the bug was still in the tree.
    last_closed: dict[int, str] = {}
    block = re.compile(r"@(?:else if|else|if|for|switch)\b[^\n{]*\{")
    while index < position:
        match = block.match(source, index)
        if match:
            header = match.group(0).strip()
            if header.startswith("@else"):
                inherited = last_closed.get(depth, "")
                header = f"{header} /* of {inherited} */"
            open_blocks.append(header)
            depth_of.append(depth)
            depth += 1
            index = match.end()
            continue
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            while depth_of and depth_of[-1] >= depth:
                depth_of.pop()
                last_closed[depth] = open_blocks.pop()
        index += 1
    return open_blocks


def test_no_search_box_lives_inside_a_block_a_load_toggles() -> None:
    """A search field that a query destroys is a search field nobody can type into.

    Reported from the running console: *"wenn ich 2 character reinschreibe, dann fängt er an zu
    suchen und ich fliege aus dem Feld raus"*. The use-case list had its input inside the `@else`
    of `@if (loading())`, so the first keystroke that reached the debounce tore the block down,
    took the input with it, and built a new one — focus gone, mid-word.

    The shape is the defect, not the one occurrence: any control that *starts* a request and sits
    inside a branch that request flips will do the same thing.
    """
    offenders: list[str] = []
    for path in TEMPLATES:
        source = path.read_text()
        for match in re.finditer(r'type="search"', source):
            guilty = [
                block
                for block in _enclosing_blocks(source, match.start())
                if "loading()" in block or "busy()" in block or "refreshing()" in block
            ]
            if guilty:
                offenders.append(f"{path.relative_to(ROOT)}: inside {guilty[-1]}")

    assert offenders == [], (
        f"a search box inside a block its own query toggles is destroyed mid-typing: {offenders}"
    )


# ---- the deployment passes the variables the code actually reads ------------------------------


def test_compose_passes_every_vault_variable_the_loader_reads() -> None:
    """`FRD-116` built Vault reading and the stack passed **none** of its variables for three days.

    The mechanism was tested against a real AppRole the whole time; what was missing was the wire,
    and nothing could see the gap because an unconfigured secret store behaves exactly like an
    absent one — it returns an empty mapping and every credential comes from the environment.

    Fixing it, the first attempt passed `VAULT_DEV_TOKEN`, which the loader does not read: it
    reads `VAULT_TOKEN`. Same defect, same day, one letter of difference. So the names are compared
    rather than remembered.
    """
    loader = (ROOT / "libs/src/aira_common/secrets.py").read_text()
    # Every `source.get("VAULT_…")` and `os.environ.get(VAULT_…)` the loader consults.
    names = set(re.findall(r'"(VAULT_[A-Z_]+)"', loader))
    # Read by the Vault *server* container, not by us.
    names -= {"VAULT_DEV_ROOT_TOKEN_ID", "VAULT_DEV_LISTEN_ADDRESS"}
    assert names, "no VAULT_* names found in the loader — this assertion would describe nothing"

    compose = (ROOT / "deploy/compose/docker-compose.apps.yml").read_text()
    missing = sorted(name for name in names if f"{name}:" not in compose)

    assert missing == [], (
        "the loader reads these and no application container is given them, so they are silently "
        f"ignored: {missing}"
    )
