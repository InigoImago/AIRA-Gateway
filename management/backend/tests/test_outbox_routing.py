"""Every event this codebase emits has a topic to travel on.

Written after a real defect: `FRD-209` added `use_case_group.granted`, `record_to_outbox` did not
know it, and the branch for an unknown type is a **deliberate silent return** — forward
compatibility, so an older Management does not crash on a newer event. The result was a grant that
was written, listed, and shown in the console, and never reached the gateway. Nothing failed
anywhere, on either plane.

That is the third instance of this shape here (`aira.rate-limits` and `aira.anomaly-rules` were
both created by nothing), and the answer has been the same each time: compare the hand-written list
against the code, in both directions.
"""

from __future__ import annotations

import ast
import pathlib

from aira_management.apps.outbox.subscriber import _TOPIC_FOR

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "aira_management"


def _emitted_event_types() -> set[str]:
    """Every literal passed as the first argument to `emit(...)` anywhere in the source."""
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "emit":
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def test_every_emitted_event_has_a_topic() -> None:
    """An event with no topic is dropped **silently** — the branch exists on purpose, and that is
    exactly what makes a missing entry invisible."""
    missing = _emitted_event_types() - set(_TOPIC_FOR)

    assert not missing, (
        f"these events are emitted and would never reach the gateway: {sorted(missing)}"
    )


def test_every_routed_event_is_actually_emitted() -> None:
    """The other direction. A route for an event nobody sends is dead configuration that reads as
    a working path — and the next person to need that event will assume it already works."""
    stale = set(_TOPIC_FOR) - _emitted_event_types()

    assert not stale, f"these routes have no emitter: {sorted(stale)}"


def test_the_scan_actually_finds_something() -> None:
    """A scan that silently matched nothing would make both assertions above vacuously true —
    which is how a check like this comes to pass while protecting nothing."""
    emitted = _emitted_event_types()

    assert len(emitted) > 10
    assert "usecase.upserted" in emitted


def test_two_grants_on_one_use_case_do_not_share_a_compaction_key() -> None:
    """The topics are **compacted**: only the last message per key survives.

    Two group grants on the same use case keyed identically would mean the second erased the first
    from the log, and a gateway rebuilding its read-model from the topic would silently lose access
    somebody actually holds.
    """
    from aira_management.apps.outbox.subscriber import record_to_outbox

    keys: list[str] = []

    class _Manager:
        @staticmethod
        def create(**kwargs: object) -> None:
            keys.append(str(kwargs["key"]))

    class Recorder:
        objects = _Manager()

    import aira_management.apps.outbox.subscriber as module

    original = module.OutboxEvent
    module.OutboxEvent = Recorder  # type: ignore[assignment,misc]
    try:
        record_to_outbox("use_case_group.granted", {"slug": "uc-a", "group": "/ai/one"})
        record_to_outbox("use_case_group.granted", {"slug": "uc-a", "group": "/ai/two"})
    finally:
        module.OutboxEvent = original  # type: ignore[assignment,misc]

    assert len(set(keys)) == 2, keys
