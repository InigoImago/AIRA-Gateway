"""The gateway's thirty-nine migrations form one chain, with one head and no gaps.

Alembic answers all three of these at `upgrade` time — *"Multiple head revisions are present"*,
*"Can't locate revision"* — which is loud and is also **the deployment**. That is the wrong place
to find out: the condition is created by a merge (two branches each adding a revision on the same
parent, both green in review because each is a valid chain on its own) and discovered by whoever
deploys next, on a system that will not start.

Hermetic on purpose. It reads the revision files rather than asking Alembic to build the graph, so
it needs no database and runs in the suite that everybody runs.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"

_REVISION = re.compile(r'^revision\s*=\s*"([^"]+)"', re.M)
_DOWN = re.compile(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', re.M)


def _graph() -> dict[str, str | None]:
    """revision → its parent, for every version file."""
    graph: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text()
        revision = _REVISION.search(source)
        down = _DOWN.search(source)
        assert revision, f"{path.name} declares no `revision`"
        assert down, f"{path.name} declares no `down_revision`"
        assert revision.group(1) not in graph, (
            f"{path.name} reuses the revision id {revision.group(1)!r}"
        )
        graph[revision.group(1)] = down.group(1)
    return graph


def test_there_are_migrations_to_check() -> None:
    """A guard on the guard: an empty graph has exactly one head — none — and passes everything."""
    assert len(_graph()) > 20, sorted(_graph())


def test_every_parent_exists() -> None:
    """A `down_revision` naming a deleted file makes `upgrade` stop at *"Can't locate revision"*
    with the deployment half applied."""
    graph = _graph()
    orphans = sorted(
        f"{revision} → {parent}"
        for revision, parent in graph.items()
        if parent is not None and parent not in graph
    )

    assert not orphans, "these name a parent revision that no file defines:\n  " + "\n  ".join(
        orphans
    )


def test_exactly_one_revision_is_a_head() -> None:
    """Two heads is what a merge of two branches produces, and `alembic upgrade head` refuses it.

    Each branch is a valid chain by itself, so nothing in either review shows the problem — it
    exists only in the union, which is precisely what a test over the whole directory can see and
    a reviewer of one pull request cannot.
    """
    graph = _graph()
    parents = {parent for parent in graph.values() if parent is not None}
    heads = sorted(revision for revision in graph if revision not in parents)

    assert len(heads) == 1, (
        f"the migration graph has {len(heads)} heads: {heads}. "
        "`alembic upgrade head` refuses this — merge them with `alembic merge`."
    )


def test_exactly_one_revision_is_the_root() -> None:
    """The mirror: two roots means a second chain that the first `upgrade` never reaches, so the
    tables it creates simply do not exist and the failure lands on a query."""
    roots = sorted(revision for revision, parent in _graph().items() if parent is None)

    assert len(roots) == 1, f"the migration graph has {len(roots)} roots: {roots}"
