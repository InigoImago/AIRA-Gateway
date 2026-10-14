"""`docs/API.md` §4 lists every configuration event, as the two planes send and apply it.

**Why this exists.** The events between Management and the gateway are the one interface no
framework describes: there is no schema module, no version field and no registry — the event type
and the field names *are* the contract (`docs/API.md` §4.2). So the page that writes the contract
down is the only description an operator, a reviewer or the author of the next event has, and a
catalogue that drifts from the code describes a contract nobody keeps. It is compared with the code
in both directions, as every hand-written list in this repository is:

1. every event type Management can send (`_TOPIC_FOR` in `apps/outbox/subscriber.py`) and every
   one the gateway applies (`HANDLERS` in `consumer/apply.py`) has a row, marked as sent and as
   applied exactly as the code is;
2. no row names an event type neither plane knows;
3. a sent event's row names the topic it travels on, and the key `record_to_outbox` actually
   writes;
4. a sent event's payload row names exactly the fields it carries — the union over every `emit`
   that sends it, its builder followed to the dict it returns.

**The payload is read from the source with `ast`**, as `test_a_model_event_is_applied_whole.py` and
`test_outbox_routing.py` read it: the builders take model instances, and building one needs a
database for a question about field names. An `emit` this cannot read fails here, by name, rather
than being skipped — a scan that quietly narrows its own scope is the failure it exists to catch.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "API.md"
MANAGEMENT = ROOT / "management" / "backend" / "src" / "aira_management"

#: The first header cells of the two tables this reads, in §4.3 and §4.4.
CATALOGUE = ("Event type", "Topic", "Key", "Management sends", "Gateway applies")
PAYLOADS = ("Events", "Fields")

#: What a table cell says for "yes".
YES = "✓"

_EVENT = re.compile(r"`([a-z_]+\.[a-z_]+)`")
_CODE = re.compile(r"`([^`]+)`")
#: A table cell boundary: a pipe, unless escaped as `\|` inside the cell.
_CELL = re.compile(r"(?<!\\)\|")


# == the code ======================================================================================


def _sent() -> dict[str, str]:
    """Every event type Management sends, and the topic it travels on."""
    from aira_management.apps.outbox.subscriber import _TOPIC_FOR

    return dict(_TOPIC_FOR)


def _applied() -> set[str]:
    """Every event type the gateway's consumer has a handler for."""
    from aira_gateway.consumer.apply import HANDLERS

    return set(HANDLERS)


@functools.cache
def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_file(module: str) -> Path:
    """The source file of an `aira_management` module, a package's `__init__` included."""
    head, *rest = module.split(".")
    assert head == "aira_management", (
        f"a payload builder imported from outside Management: {module}"
    )
    base = MANAGEMENT.joinpath(*rest)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    raise AssertionError(f"no source file for {module}")


def _keys(literal: ast.Dict, where: str) -> set[str]:
    """The keys of a dict literal — every one a string, or this test cannot say what is sent."""
    keys: set[str] = set()
    for key in literal.keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            f"{where}: a payload key this test cannot read (`**` or a computed key). Write the "
            "payload as a literal or in a builder returning one, or teach this test the new form."
        )
        keys.add(key.value)
    return keys


def _built_by(path: Path, name: str, hops: int = 0) -> set[str]:
    """The keys of the dict ``name`` returns — followed through the imports to where it is defined,
    since a builder is often imported through a package's re-export."""
    assert hops < 5, f"`{name}` is re-exported more than five times from {path}"
    tree = _parsed(path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            returned = [
                statement.value
                for statement in ast.walk(node)
                if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict)
            ]
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            assert returned, f"{where}: `{name}` returns no dict literal this test can read"
            return set().union(*(_keys(literal, where) for literal in returned))
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if (alias.asname or alias.name) == name:
                assert node.level == 0 and node.module, (
                    f"{path.relative_to(ROOT)}: `{name}` comes from a relative import"
                )
                return _built_by(_module_file(node.module), alias.name, hops + 1)
    raise AssertionError(f"`{name}` is neither defined nor imported in {path.relative_to(ROOT)}")


@functools.cache
def _emitted_fields() -> dict[str, frozenset[str]]:
    """Every field each event type carries, over every `emit` that sends it.

    Migrations are left out: the few that write events do so directly, once, with the payload as
    it stood at that migration — history, not the contract a running Management keeps.
    """
    fields: dict[str, set[str]] = {}
    for path in sorted(MANAGEMENT.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(_parsed(path)):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first, payload = node.args[0], node.args[1]
            if called != "emit" or not (
                isinstance(first, ast.Constant) and isinstance(first.value, str)
            ):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if isinstance(payload, ast.Dict):
                keys = _keys(payload, where)
            elif isinstance(payload, ast.Call) and isinstance(payload.func, ast.Name):
                keys = _built_by(path, payload.func.id)
            else:
                raise AssertionError(
                    f"{where}: the payload of `{first.value}` is neither a dict literal nor a call "
                    "to a builder, so this test cannot say which fields it carries"
                )
            fields.setdefault(first.value, set()).update(keys)
    return {event: frozenset(keys) for event, keys in fields.items()}


# == the page ======================================================================================


def _cells(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in _CELL.split(line.strip().strip("|"))]


def _table(header: tuple[str, ...]) -> list[list[str]]:
    """The data rows of the table in docs/API.md whose header starts with ``header``.

    Found by its header, not by a heading or a line number, so renumbering §4 does not lose it — and
    a table that is gone fails here by name, instead of every check below passing on no rows.
    """
    lines = REFERENCE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("|") and tuple(_cells(line)[: len(header)]) == header:
            rows: list[list[str]] = []
            for row in lines[index + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append(_cells(row))
            return rows
    raise AssertionError(f"docs/API.md has no table whose header begins {' | '.join(header)}")


def _catalogue() -> dict[str, dict[str, str]]:
    """§4.3, one entry per event type it lists. A type listed twice fails here."""
    rows: dict[str, dict[str, str]] = {}
    for cells in _table(CATALOGUE):
        named = _EVENT.fullmatch(cells[0])
        assert named, f"a row of the event catalogue names no event type: {cells}"
        event = named.group(1)
        assert event not in rows, f"the event catalogue lists `{event}` twice"
        rows[event] = dict(zip(CATALOGUE, cells, strict=False))
    return rows


def _payloads() -> dict[str, frozenset[str]]:
    """§4.4: the fields each event type is documented to carry. A type in two rows fails here."""
    documented: dict[str, frozenset[str]] = {}
    for cells in _table(PAYLOADS):
        events = _EVENT.findall(cells[0])
        assert events, f"a row of the payload table names no event type: {cells}"
        for event in events:
            assert event not in documented, f"the payload table lists `{event}` twice"
            documented[event] = frozenset(_CODE.findall(cells[1]))
    return documented


def _plain(cell: str) -> str:
    """A cell's one code span, or the cell itself: `` `slug|username` `` is `slug|username`."""
    spans = _CODE.findall(cell)
    return spans[0] if len(spans) == 1 else cell


# == the checks ====================================================================================


def test_both_planes_and_the_page_have_events_to_compare() -> None:
    """A guard on the guard: two empty sets agree perfectly, and a table this cannot find would
    leave every check below comparing nothing."""
    assert len(_sent()) >= 15, sorted(_sent())
    assert len(_applied()) >= 15, sorted(_applied())
    assert len(_emitted_fields()) >= 15, sorted(_emitted_fields())
    assert len(_catalogue()) >= 15, sorted(_catalogue())
    assert len(_payloads()) >= 15, sorted(_payloads())


def test_every_event_either_plane_knows_is_in_the_catalogue() -> None:
    known = set(_sent()) | _applied()
    missing = known - set(_catalogue())

    assert not missing, (
        f"Management sends or the gateway applies these, and docs/API.md §4.3 does not list them: "
        f"{sorted(missing)}. Add a row — topic, key, whether each plane knows it, what it means — "
        "and its fields to §4.4: the page is the only description of this contract there is."
    )


def test_the_catalogue_lists_no_event_neither_plane_knows() -> None:
    """The other direction: a row for an event nobody sends or applies reads as a working path."""
    known = set(_sent()) | _applied()
    invented = (set(_catalogue()) | set(_payloads())) - known

    assert not invented, (
        f"docs/API.md §4 lists {sorted(invented)}, which neither plane sends or applies. The event "
        "was renamed or removed: correct the row, or delete it."
    )


def test_each_row_says_which_plane_sends_and_which_applies() -> None:
    """Asked of both columns, because the asymmetries are the rows a reader needs:
    `pipeline.deleted` is applied and never sent, and an event sent and never applied would be a
    change the console shows and the gateway never enforces."""
    sent, applied = set(_sent()), _applied()
    wrong = [
        f"{event}: sends {row['Management sends']!r}, applies {row['Gateway applies']!r}"
        for event, row in _catalogue().items()
        if (row["Management sends"] == YES) != (event in sent)
        or (row["Gateway applies"] == YES) != (event in applied)
    ]

    assert not wrong, (
        f"In docs/API.md §4.3 these marks disagree with the code ({YES} where it does, — where it "
        "does not):\n  " + "\n  ".join(wrong)
    )


def test_each_sent_event_names_its_topic() -> None:
    rows = _catalogue()
    wrong = {
        event: (_plain(rows[event]["Topic"]), topic)
        for event, topic in _sent().items()
        if event in rows and _plain(rows[event]["Topic"]) != topic
    }

    assert not wrong, (
        "docs/API.md §4.3 names the wrong topic for these (documented, actual): "
        f"{wrong}. The topic comes from `_TOPIC_FOR` in `apps/outbox/subscriber.py`."
    )


def test_each_sent_event_names_the_key_the_outbox_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key decides what a compacted topic keeps, so a reader replaying one needs the real rule.

    Asked of `record_to_outbox` itself rather than of a second copy of its rule: each event is
    recorded with every field it carries set to the field's own name, and the key it writes is
    then spelled in field names — `slug|username`. Only the row is intercepted, as
    `test_outbox_routing.py` does, so the rule that runs is the one Management runs.
    """
    import aira_management.apps.outbox.subscriber as subscriber

    written: list[str] = []

    class _Rows:
        @staticmethod
        def create(**row: Any) -> None:
            written.append(str(row["key"]))

    class _Outbox:
        objects = _Rows()

    monkeypatch.setattr(subscriber, "OutboxEvent", _Outbox)
    rows, fields = _catalogue(), _emitted_fields()
    wrong: dict[str, tuple[str, str]] = {}
    for event in sorted(set(_sent()) & set(rows)):
        written.clear()
        subscriber.record_to_outbox(event, {field: field for field in sorted(fields[event])})
        documented = _plain(rows[event]["Key"])
        if documented != written[0]:
            wrong[event] = (documented, written[0])

    assert not wrong, (
        "docs/API.md §4.3 names the wrong key for these (documented, written): "
        f"{wrong}. A record's key is the first of its payload's `id`, `prefix`, `slug`, `name`, "
        "`use_case`, with `|username` or `|group` for memberships and grants."
    )


def test_each_sent_event_names_exactly_the_fields_it_carries() -> None:
    """Both directions. A field the page leaves out is one a consumer of the contract cannot know
    about; one it names that is never sent is one somebody waits for."""
    documented = _payloads()
    emitted = _emitted_fields()
    wrong = []
    for event in sorted(_sent()):
        if event not in documented:
            wrong.append(f"{event}: no row in the payload table")
            continue
        carried = emitted.get(event, frozenset())
        if documented[event] != carried:
            wrong.append(
                f"{event}: not named {sorted(carried - documented[event])}, "
                f"never sent {sorted(documented[event] - carried)}"
            )

    assert not wrong, (
        "docs/API.md §4.4 and the payloads Management sends disagree:\n  "
        + "\n  ".join(wrong)
        + "\n\nThe fields are the union over every `emit` of the event, read from its builder "
        "(`apps/usecases/views/payloads.py` and its siblings) or the literal beside the call."
    )
