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
from aira_gateway.consumer.worker import apply_one_message, consume_forever
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


# ---- what a message can be before anybody has read it ------------------------------------------


@dataclass
class _Raw:
    """A message as aiokafka hands it over with no deserializer: bytes, or a tombstone's `None`."""

    value: bytes | None
    headers: list[tuple[str, bytes | None]]
    topic: str = USECASE_TOPIC
    partition: int = 0
    offset: int = 0


class _Broker:
    """A consumer yielding fixed messages, as `consume_forever` iterates one."""

    def __init__(self, messages: list[_Raw]) -> None:
        self._messages = messages

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def __aiter__(self):  # noqa: ANN204
        for message in self._messages:
            yield message


def _typed(value: bytes | None, *extra: tuple[str, bytes | None]) -> _Raw:
    return _Raw(value=value, headers=[(EVENT_TYPE_HEADER, b"usecase.upserted"), *extra])


async def test_no_shape_of_message_stops_the_consumer(sessionmaker: Any) -> None:
    """**Every step that depends on the message's shape runs inside the guard.** The value is
    decoded there, not by a deserializer inside the client — which raised on bytes that are not
    JSON and on a compacted topic's tombstone before the guard was ever reached — and the headers
    are read without trusting their bytes. Each of these ended the loop, and the restarted
    consumer read the same message and died again, while revoked keys went on working.

    The last message is the claim: distribution continues past all of them.
    """
    poison = [
        _typed(b"not json"),
        _typed(None),
        _typed(b"[1, 2]"),
        _Raw(value=b'{"slug": "x"}', headers=[(EVENT_TYPE_HEADER, b"\xff\xfe")]),
        _Raw(value=b'{"slug": "x"}', headers=[(EVENT_TYPE_HEADER, None)]),
    ]
    # A header of no use to us does not cost a good event its application.
    binary_header = _typed(b'{"slug": "eins", "name": "Eins"}', ("schema", b"\x00\x9f"))
    good_after = _typed(b'{"slug": "zwei", "name": "Zwei"}')

    seen = await consume_forever(_Broker([*poison, binary_header, good_after]), sessionmaker)

    assert seen == len(poison) + 2
    assert await _slugs(sessionmaker) == ["eins", "zwei"]
