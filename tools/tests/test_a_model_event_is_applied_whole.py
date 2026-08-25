"""Every field Management puts in a model event is a field the gateway applies.

The catalog is the one configuration that travels as a **wide** event: a model's declaration is
fifteen or so fields, published by `catalog/views._payload` and applied by
`consumer/apply._upsert_model`. Both halves are hand-written lists, in different languages, in
different repositories' worth of code, and **nothing compared them**.

The failure that leaves is the shape this project has already met four times as *"two correct
halves and no wire between them"*, and here it is completely silent. Add a field to Management:
the console offers it, the serializer accepts it, the database stores it, the event carries it over
Kafka — and the consumer, whose `_DECLARATION_DEFAULTS` decides what it copies, drops it on the
floor. No error, no log line, no failing test. The model is catalogued with the field in one plane
and without it in the other, and the only symptom is a feature that does nothing.

Found while adding `context_window` (`FRD-132` §11), by hand, to both halves — which is when it
became obvious that the second half is only ever remembered because somebody remembers it.

## What this reads

`_payload` is a single `return {...}` of string keys, and `_upsert_model` applies two sets: keys it
names explicitly, and `_DECLARATION_DEFAULTS`. Both are read from the source with `ast`, not
imported: Django needs a configured settings module, and this test would then be one such
variable away from not running at all — which is a guard that cannot fail.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "management/backend/src/aira_management/apps/catalog/views.py"
APPLY = ROOT / "gateway/src/aira_gateway/consumer/apply.py"

#: Keys the event carries that the read-model deliberately does not store, each with the reason.
#: **A decision, not a waiver** — the wrong way to use this is to silence a red test.
NOT_APPLIED = {
    # The primary key. `_upsert_model` looks the row up by it rather than assigning it.
    "name",
}


def _dict_keys_returned_by(path: Path, function: str) -> set[str]:
    """The string keys of the dict literal a function returns."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or node.name != function:
            continue
        for statement in ast.walk(node):
            if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict):
                return {
                    key.value
                    for key in statement.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    raise AssertionError(f"no dict-returning `{function}` in {path}")


def _keys_assigned_in(path: Path, name: str) -> set[str]:
    """The string keys of a module-level dict assigned to `name`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target, value = first.id, node.value
        if target == name and isinstance(value, ast.Dict):
            return {
                key.value
                for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError(f"no module-level dict `{name}` in {path}")


def _applied_keys() -> set[str]:
    """Fields `_upsert_model` writes: the ones it names, plus the declaration defaults.

    The explicit ones are read as the *subscripts of the payload* it reads — `payload.get("x")` —
    because the local dict it builds uses the read-model's column names, which are not always the
    event's (`input_price_per_million` becomes `..._nanos`). What has to agree is the vocabulary on
    the wire, so the wire is what is read.
    """
    tree = ast.parse(APPLY.read_text(encoding="utf-8"))
    named: set[str] = set()
    for node in ast.walk(tree):
        # `async def` is a **different AST node**, and the first version of this checked only
        # `FunctionDef` — so it read nothing at all. Caught by this file's own vacuity assertion
        # rather than by review, which is the reason that assertion is there.
        if (
            not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            or node.name != "_upsert_model"
        ):
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "payload"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                named.add(call.args[0].value)
    assert named, "read no `payload.get(...)` in `_upsert_model` — the scan describes nothing"
    return named | _keys_assigned_in(APPLY, "_DECLARATION_DEFAULTS")


def test_both_halves_are_where_this_expects_them() -> None:
    """Without this, a moved file turns every assertion below into a green nothing."""
    published = _dict_keys_returned_by(VIEWS, "_payload")

    assert len(published) > 10, sorted(published)
    assert {"name", "capabilities", "max_output_tokens"} <= published, sorted(published)
    assert "capabilities" in _applied_keys()


def test_the_gateway_applies_every_field_management_publishes() -> None:
    published = _dict_keys_returned_by(VIEWS, "_payload")
    dropped = published - _applied_keys() - NOT_APPLIED

    assert dropped == set(), (
        f"Management publishes {sorted(dropped)} in the model event and the gateway's consumer "
        "never applies them. Nothing reports this at runtime: the console offers the field, the "
        "database stores it, Kafka carries it, and the read-model the request path actually asks "
        "is missing it.\n\nAdd them to `_DECLARATION_DEFAULTS` in `consumer/apply.py`, or to "
        "NOT_APPLIED here with the reason the gateway does not want them."
    )


def test_the_gateway_does_not_wait_for_a_field_nobody_sends() -> None:
    """The other direction, which is quieter still.

    A default for a key the event never carries is a field that reads as *declarable* — it is in
    the read-model, the request path may branch on it, and it is permanently at its default because
    no Management ever sends it. That is `LESSONS.md`'s "a dead definition is a rule the module
    appears to have", one plane along.
    """
    published = _dict_keys_returned_by(VIEWS, "_payload")
    awaited = _keys_assigned_in(APPLY, "_DECLARATION_DEFAULTS") - published

    assert awaited == set(), (
        f"the consumer has defaults for {sorted(awaited)}, which no model event carries — so they "
        "are permanently at their default and read as something somebody could declare."
    )
