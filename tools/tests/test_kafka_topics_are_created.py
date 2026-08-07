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

#: Where the list is repeated. Each is a real thing an operator or a container relies on.
PLACES = {
    "the Makefile target": ROOT / "Makefile",
    "the Compose topic-creation step": ROOT / "deploy/compose/docker-compose.apps.yml",
    "the deployment documentation": ROOT / "docs/DEPLOYMENT.md",
}


def _declared_topics() -> set[str]:
    return {
        value
        for name, value in vars(kafka).items()
        if name.endswith("_TOPIC") and isinstance(value, str) and value.startswith("aira.")
    }


def test_the_source_of_truth_actually_declares_topics() -> None:
    """A guard on the guard: if the naming convention changed, this file would otherwise pass by
    checking nothing."""
    assert len(_declared_topics()) >= 7


@pytest.mark.parametrize("where", sorted(PLACES))
def test_every_declared_topic_is_created(where: str) -> None:
    text = PLACES[where].read_text()
    missing = sorted(topic for topic in _declared_topics() if topic not in text)
    assert not missing, (
        f"{where} does not know about {', '.join(missing)}. A topic nothing creates fails "
        "silently: the relay publishes, the broker drops it, and no error reaches anybody."
    )


def test_nothing_creates_a_topic_the_code_never_publishes_to() -> None:
    """The other direction. A topic left behind after a rename is a partition nobody reads, and it
    looks exactly like one that is simply quiet."""
    declared = _declared_topics()
    for where, path in PLACES.items():
        # The negative lookahead keeps `aira.example.com` out: a hostname is not a topic, and a
        # check that fails on documentation prose is a check somebody deletes.
        found = set(re.findall(r"aira\.[a-z-]+(?![a-z0-9.-])", path.read_text()))
        stray = sorted(found - declared)
        assert not stray, f"{where} creates {', '.join(stray)}, which nothing publishes to"
