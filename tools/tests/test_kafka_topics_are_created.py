"""Every topic the code publishes to is a topic something creates.

Written after the *second* time this failed the same way. `FRD-405` shipped `aira.rate-limits`
without adding it to the topic-creation step; `FRD-500` shipped `aira.anomaly-rules` the same way,
and a live round found it only because a rule authored through Management never arrived at the
gateway.

The failure is **silent by construction**: Management writes its outbox, the relay publishes, and
the broker (with auto-creation off) drops it. Nothing returns an error to anybody. The only trace is
a line in a consumer log — `Topic ... not found in cluster metadata` — repeated forever in a
container nobody is watching.

The topic names have a single source of truth in `aira_common.kafka`. They are also written out by
hand in three other places, and a fourth copy is not the fix — a check that the copies agree is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aira_common import kafka

ROOT = Path(__file__).resolve().parents[2]

#: Where the list is repeated as a creation loop over base names, staged when the loop runs.
LOOPS = {
    "the Makefile target": ROOT / "Makefile",
    "the Compose topic-creation step": ROOT / "deploy/compose/docker-compose.apps.yml",
}
#: Where it is written out in full, as an operator reads it.
DOCUMENTS = {"the deployment documentation": ROOT / "docs/DEPLOYMENT.md"}


def _declared_topics() -> set[str]:
    return {
        value
        for name, value in vars(kafka).items()
        if name.endswith("_TOPIC") and isinstance(value, str) and value.startswith("aira.")
    }


def _looped(path: Path) -> set[str]:
    """The topics a creation loop makes without a stage: `aira.` and each name it iterates."""
    match = re.search(r"for t in ([a-z -]+);\s*do", path.read_text())
    assert match, f"no topic-creation loop in {path.name} — has its shape changed?"
    return {f"aira.{name}" for name in match.group(1).split()}


def test_the_source_of_truth_actually_declares_topics() -> None:
    """A guard on the guard: if the naming convention changed, this file would otherwise pass by
    checking nothing."""
    assert len(_declared_topics()) >= 7


def test_both_planes_read_one_list_of_every_topic() -> None:
    """Management publishes to `CONFIG_TOPICS` and the gateway subscribes to it; a topic missing
    from it is one side not knowing about the other."""
    assert set(kafka.CONFIG_TOPICS) == _declared_topics()
    assert len(kafka.CONFIG_TOPICS) == len(set(kafka.CONFIG_TOPICS))


@pytest.mark.parametrize("where", sorted(LOOPS))
def test_every_declared_topic_is_created_and_nothing_else(where: str) -> None:
    """Both directions: a topic nothing creates fails silently — the relay publishes, the broker
    drops it — and a topic left after a rename is a partition nobody reads."""
    looped = _looped(LOOPS[where])
    assert looped == _declared_topics(), (
        f"{where} creates {sorted(looped)} and the code declares {sorted(_declared_topics())}"
    )


@pytest.mark.parametrize("where", sorted(DOCUMENTS))
def test_every_declared_topic_is_documented(where: str) -> None:
    text = DOCUMENTS[where].read_text()
    missing = sorted(topic for topic in _declared_topics() if topic not in text)
    assert not missing, f"{where} does not name {', '.join(missing)}"


def test_the_documentation_names_no_topic_the_code_never_publishes_to() -> None:
    declared = _declared_topics()
    for where, path in DOCUMENTS.items():
        # The negative lookahead keeps `aira.example.com` out: a hostname is not a topic, and a
        # check that fails on documentation prose is a check somebody deletes.
        found = set(re.findall(r"aira\.[a-z-]+(?![a-z0-9.-])", path.read_text()))
        stray = sorted(found - declared)
        assert not stray, f"{where} names {', '.join(stray)}, which nothing publishes to"
