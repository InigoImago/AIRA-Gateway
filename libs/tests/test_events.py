from aira_common.events import Event, EventPublisher, InMemoryEventPublisher


async def test_in_memory_publisher_records() -> None:
    pub = InMemoryEventPublisher()
    event = Event(type="usage.recorded", payload={"tokens": 10})
    await pub.publish("usage", event, key="user-1")

    assert pub.published == [("usage", event, "user-1")]
    assert pub.events_for("usage") == [event]
    assert pub.events_for("other") == []


async def test_in_memory_publisher_satisfies_protocol() -> None:
    pub = InMemoryEventPublisher()
    assert isinstance(pub, EventPublisher)


def test_event_defaults() -> None:
    event = Event(type="x")
    assert event.version == 1
    assert event.payload == {}
