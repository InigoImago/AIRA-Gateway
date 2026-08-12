"""One config event that cannot be applied does not stop the others.

The consumer loop called `apply_event` straight out of `async for`, so any failure left the loop,
stopped the consumer and ended the process. Every handler indexes its payload — `payload["slug"]`,
`payload["prefix"]`, `payload["username"]` — so a single malformed event was enough: a field
renamed by a newer Management, a truncated value, a database that blinked.

**The consequence is worse than the crash, and it is silent.** The container restarts, reads the
same message and dies again — a poison pill — while the gateway goes on serving perfectly from a
read-model that has quietly stopped being updated. So a **revoked API key keeps working**, a new
budget never arrives, a deleted use case still answers. Nothing on any screen says that
configuration stopped flowing; the only symptom is a container's restart count, which is the one
place nobody is looking during an incident.

And the opposite outcome is available too. Offsets are auto-committed on a timer, so the commit
can move past the bad message before the crash, in which case the event is simply lost. Which of
the two happens is a race between a timer and an exception.

The rule this file pins is therefore not "nothing fails" but **"a failure is contained and named"**:
the bad event is skipped with its topic, partition and offset, and every event after it is still
applied. Stopping the world for one malformed row is not the safer choice for a component whose
job is to deliver revocations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from aira_common.kafka import EVENT_TYPE_HEADER, USECASE_TOPIC
from aira_gateway.consumer.worker import apply_one_message
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import UseCaseRead


@dataclass
class _Message:
    """What aiokafka hands the loop, reduced to what the loop reads."""

    value: dict[str, Any]
    event_type: str | None = "usecase.upserted"
    topic: str = USECASE_TOPIC
    partition: int = 0
    offset: int = 0
    headers: list[tuple[str, bytes]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.event_type is not None:
            self.headers = [(EVENT_TYPE_HEADER, self.event_type.encode())]


@pytest.fixture
async def sessionmaker() -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    return build_sessionmaker(engine)


async def _slugs(sessionmaker: Any) -> list[str]:
    async with sessionmaker() as session:
        return sorted((await session.execute(select(UseCaseRead.slug))).scalars())


async def test_a_malformed_event_is_skipped_and_the_next_one_still_arrives(
    sessionmaker: Any,
) -> None:
    """The property, stated as a sequence: bad in the middle, and the ones around it land.

    Written with three messages rather than one, because "it did not raise" is not the claim. The
    claim is that **distribution continues**, and only a later message can show that.
    """
    good_first = _Message({"slug": "eins", "name": "Eins"})
    poison = _Message({"name": "no slug at all"})  # every handler indexes its payload
    good_after = _Message({"slug": "zwei", "name": "Zwei"})

    applied = [await apply_one_message(sessionmaker, m) for m in (good_first, poison, good_after)]

    assert applied == ["usecase.upserted", None, "usecase.upserted"]
    assert await _slugs(sessionmaker) == ["eins", "zwei"], (
        "an event after the bad one never arrived — the consumer stopped, and from here on a "
        "revoked key would keep working with nothing to say so"
    )


async def test_an_event_with_no_type_header_is_skipped(sessionmaker: Any) -> None:
    """It always was skipped; what it was not is *named*.

    Three defects in this repository had one mechanism: a `return` for something unrecognised,
    with nothing written down. On a topic only we publish to, a message with no type header means
    somebody else is producing — which is worth a line in a log whatever else it means.
    """
    assert await apply_one_message(sessionmaker, _Message({"slug": "x"}, event_type=None)) is None
    assert await _slugs(sessionmaker) == []


async def test_an_unknown_event_type_is_still_forward_compatible(sessionmaker: Any) -> None:
    """The deliberate exception, pinned so the containment above cannot swallow it by accident.

    An older gateway meeting a newer Management's event type must ignore it and carry on — that is
    what `apply_event`'s "unknown types are ignored" is for, and it is a *success*, not a failure
    to be logged as one. It returns the type it handled, because it handled it: by deciding there
    was nothing to do.
    """
    message = _Message({"slug": "x"}, event_type="something.newer")

    assert await apply_one_message(sessionmaker, message) == "something.newer"
    assert await _slugs(sessionmaker) == []
