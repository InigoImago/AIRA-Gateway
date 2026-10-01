"""The history generator moves a clock, so what it may touch is checked from the source.

`tools/demo_history.py` drives real traffic and then backdates the rows it created (`FRD-626` §12).
That second half is an `UPDATE` against `request_logs` — the audit trail — which is the one table in
this system nothing is supposed to edit. The properties below are the containment, and they are
readable without a stack, which is the point: a demo helper is exactly the thing nobody runs a live
suite against before a walkthrough.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
HISTORY = ROOT / "tools/demo_history.py"
MAKEFILE = ROOT / "Makefile"
SEED = ROOT / "management/backend/src/aira_management/apps/seed/contributions/showcase_data.py"


def _constant(source: str, name: str) -> ast.expr | None:
    for node in ast.parse(source).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _weighted_slugs() -> list[str]:
    node = _constant(HISTORY.read_text(), "WEIGHTS")
    assert isinstance(node, ast.Dict), "WEIGHTS is not a literal dict any more"
    slugs = [key.value for key in node.keys if isinstance(key, ast.Constant)]
    assert slugs, "no slugs parsed; this whole file would pass vacuously"
    return [str(slug) for slug in slugs]


def test_it_only_ever_sends_as_a_use_case_the_showcase_creates() -> None:
    """Traffic as a use case that does not exist is refused, and a refusal it did not mean to
    produce is a history of a gateway that answered nothing."""
    seeded = SEED.read_text()

    for slug in _weighted_slugs():
        assert f'"slug": "{slug}"' in seeded, (
            f"'{slug}' is not one of the showcase's use cases, so every request it sends is refused"
        )


def test_the_update_is_bounded_by_the_use_cases_it_names() -> None:
    """Never "recent rows", never "all". The clock is being changed on an audit trail, and the one
    thing that keeps that acceptable is that it cannot reach a row somebody else's traffic wrote."""
    source = HISTORY.read_text()

    assert "DEMO_SLUGS = tuple(WEIGHTS)" in source, (
        "the list of use cases it may touch is no longer the list it sends as"
    )
    assert "use_case = ANY(:slugs)" in source, "the row selection is not bounded by use case"
    assert "created_at >= :marker" in source, "the row selection is not bounded in time"
    # The update itself takes one id at a time, so there is no statement that could match a set.
    assert "UPDATE request_logs SET created_at = :when WHERE id = :id" in source


def test_a_day_whose_rows_it_cannot_match_is_reported_and_left_alone() -> None:
    """The failure mode worth refusing: if something else wrote to a demo use case while this ran,
    pairing rows with instants by position would move a timestamp that is not this run's."""
    source = HISTORY.read_text()

    assert "if len(rows) != sent:" in source
    assert "leaving this day where it is" in source


def test_the_showcase_builds_its_history_before_it_clears_the_counters() -> None:
    """Order, and it is the whole reason this runs where it does. The history spends budgets as it
    goes; the reset after it is what makes the bars a walkthrough looks at belong to the showcase's
    own run rather than to the last day of a month that was invented a minute earlier."""
    body = MAKEFILE.read_text()
    showcase = body[body.index("\nshowcase:") : body.index("\nshowcase-traffic:")]

    assert showcase.index("demo_history.py") < showcase.index("demo_reset_usage.py"), (
        "the showcase clears the counters before building the history, so the bars it then shows "
        "belong to whatever the history spent last"
    )


def test_it_clears_the_counters_between_its_own_days() -> None:
    """Each backdated day is its own budget period. Without this the showcase's deliberately tight
    limits (`FRD-130` FR-3) refuse everything after the first day, and the history is a wall of
    429s — real rows, truthfully recorded, demonstrating nothing."""
    source = HISTORY.read_text()

    assert "await demo_reset_usage.reset()" in source
    # Inside the per-day loop, not once before it.
    loop = source.index("for days_ago, count in plan:")
    assert source.index("await demo_reset_usage.reset()", loop) > loop


def test_nothing_served_is_a_failure() -> None:
    """`FRD-130` §4c, in a second script: a demo helper that reports success over a screen of
    refusals is worse than one that fails."""
    assert "if not served:" in HISTORY.read_text()
