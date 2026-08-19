"""What Management publishes and what the gateway applies are one vocabulary, checked both ways.

There are **three** statements of it, and only two were ever compared:

1. the `emit("…")` calls across Management;
2. `_TOPIC_FOR` in `apps/outbox/subscriber.py`, which decides the Kafka topic;
3. the `elif event_type == "…"` chain in `gateway/consumer/apply.py`, which applies it.

`test_outbox_routing.py` compares 1 against 2, in both directions, by walking the AST — and it
found a real defect doing so. Nothing compared either of them against **3**, which is the end of
the wire and the only one that changes what the running system does.

The failure that leaves is quiet and expensive. Add an event type to Management, route it to a
topic, and forget the consumer: the control plane records the change, the console shows it, Kafka
carries it, and the gateway does not apply it. A budget that never arrives, a released model that
never lands. The only symptom is the two planes disagreeing, with nothing anywhere connecting them
— the shape this repository has already met four times as *"two correct halves and no wire between
them"*.

The consumer now logs `config_event_not_applied` for anything it does not handle, which makes the
failure greppable at runtime. This makes it fail at CI instead.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "management" / "backend" / "src" / "aira_management"
APPLY = ROOT / "gateway" / "src" / "aira_gateway" / "consumer" / "apply.py"

#: Types one plane knows and the other deliberately does not, each with the reason it is exempt.
#: **An entry is a decision, not a waiver** — the wrong way to use this set is to add a name to
#: make a red test green, which is how the exemption lists this project has rejected elsewhere
#: (`ADR-0015`'s "an exemption is a list, never a `return []`") start.
DELIBERATE = {
    # The gateway keeps a handler for an event Management cannot send: it has no endpoint that
    # deletes a pipeline — clearing one is a PUT with no steps, and a use case's pipeline goes
    # with the use case. The branch is forward compatibility, which is what lets an older gateway
    # survive a rolling update against a newer Management that grows the endpoint (`FRD-127`).
    # `_TOPIC_FOR` says the same thing from the other side, and says why.
    "pipeline.deleted": "applied but never emitted — forward compatibility, see `_TOPIC_FOR`",
}


def _emitted() -> set[str]:
    """Every literal first argument to an `emit(...)` call anywhere in Management.

    The same walk `test_outbox_routing` does, repeated rather than imported: that file lives in
    Management's own suite and this one spans both planes, and a test importing another test's
    private helper is a dependency nobody expects to have.
    """
    found: set[str] = set()
    for path in MANAGEMENT.rglob("*.py"):
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


def _applied() -> set[str]:
    """Every event type the gateway's consumer branches on.

    Read from the source rather than by calling `apply_event` with every candidate: the chain is
    what a reader sees, and a branch that exists but is unreachable would pass a behavioural probe
    by raising nothing.
    """
    source = APPLY.read_text()
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id == "event_type"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                found.add(comparator.value)
    return found


def test_both_scans_find_something() -> None:
    """A guard on the guard: two empty sets are equal, and would report perfect agreement."""
    assert len(_emitted()) > 10, sorted(_emitted())
    assert len(_applied()) > 10, sorted(_applied())


def test_every_event_management_publishes_is_one_the_gateway_applies() -> None:
    """The direction that costs. An emitted event nothing applies is a configuration change the
    operator made, the console shows, and the data plane never received."""
    unhandled = sorted(_emitted() - _applied() - set(DELIBERATE))

    assert not unhandled, (
        "Management publishes these and the gateway's consumer has no branch for them:\n  "
        + "\n  ".join(unhandled)
        + "\n\nThe change is recorded, routed and never applied — a control an operator believes "
        "is in force. Add a branch in `gateway/consumer/apply.py`, or record the reason in "
        "`DELIBERATE` above."
    )


def test_every_branch_the_gateway_carries_is_one_management_can_send() -> None:
    """The other direction: a branch for an event nothing sends reads as a working path.

    Not an error by itself — forward compatibility is a real reason to carry one — but it has to
    be a *stated* reason, or the next reader treats dead configuration as supported.
    """
    unsent = sorted(_applied() - _emitted() - set(DELIBERATE))

    assert not unsent, (
        "The gateway applies these and nothing in Management sends them:\n  "
        + "\n  ".join(unsent)
        + "\n\nEither Management lost an `emit`, or the branch is forward compatibility — in "
        "which case say so in `DELIBERATE` above, as `pipeline.deleted` does."
    )


def test_the_exemptions_are_still_exemptions() -> None:
    """A `DELIBERATE` entry that has stopped being an asymmetry is a comment nobody will re-read.

    Management growing the endpoint that sends `pipeline.deleted` should remove the entry, not
    leave a note explaining why an event that now exists does not.
    """
    both = _emitted() & _applied()
    stale = sorted(set(DELIBERATE) & both)

    assert not stale, (
        f"These are listed as deliberate asymmetries and are now handled on both sides: {stale}. "
        "Remove the entry — the reason it records is no longer true."
    )


def test_the_consumer_says_so_when_it_applies_nothing() -> None:
    """Tolerating an unknown event is right; doing it in silence is what this file exists about.

    Asserted on the source because the alternative — driving a real consumer with an invented
    event type — needs Kafka and a database to prove a one-line property about a log call.
    """
    source = APPLY.read_text()

    assert "config_event_not_applied" in source, (
        "The consumer's fallthrough is silent again. An event this gateway does not apply is a "
        "governance control that did not take effect, and the only symptom is the two planes "
        "disagreeing."
    )
    # And the log must sit on the fallthrough rather than somewhere convenient.
    fallthrough = source[source.index("    else:", source.index("elif event_type ==")) :]
    assert "config_event_not_applied" in fallthrough[:1500], (
        "the log line has drifted away from the branch it reports on"
    )


def test_the_log_records_names_and_not_values() -> None:
    """`FRD-122`: configuration payloads carry `client_secret`-shaped fields. The line names which
    keys arrived, never what was in them."""
    source = APPLY.read_text()
    call = source[source.index("config_event_not_applied") :][:400]

    assert "sorted(payload)" in call, "the fields are listed by name"
    assert "payload=payload" not in call and "**payload" not in call, call


def test_the_module_path_this_file_reads_still_exists() -> None:
    """The scans read two files by path. A move would make both directions pass by finding
    nothing, which `test_both_scans_find_something` catches — this names the cause."""
    assert APPLY.exists(), APPLY
    assert MANAGEMENT.is_dir(), MANAGEMENT
    if str(ROOT) not in sys.path:  # pragma: no cover - import hygiene only
        sys.path.insert(0, str(ROOT))
    assert re.search(r"^IGNORED_EVENT_TYPES", APPLY.read_text(), re.M), (
        "the consumer's stated set of knowingly-ignored types is gone"
    )
