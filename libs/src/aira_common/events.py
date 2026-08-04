"""Event abstraction for inter-component Kafka communication.

This module defines the *interface* both components program against. A real
aiokafka-backed publisher is introduced when event emission lands (Phase 1,
FR-GW-12); until then the in-memory implementation powers unit tests and demo mode.
Keeping the transport behind :class:`EventPublisher` means no service imports a Kafka
client directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Envelope for a domain event published to Kafka.

    ``type`` and ``version`` identify the schema; ``payload`` carries the body. The
    concrete per-topic schemas are defined in their respective FRDs.
    """

    type: str
    version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class EventPublisher(Protocol):
    """Publishes events to a named topic. Implementations may be async transports."""

    async def publish(self, topic: str, event: Event, *, key: str | None = None) -> None: ...


class InMemoryEventPublisher:
    """Test/demo publisher that records events instead of sending them to Kafka."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Event, str | None]] = []

    async def publish(self, topic: str, event: Event, *, key: str | None = None) -> None:
        self.published.append((topic, event, key))

    def events_for(self, topic: str) -> list[Event]:
        """Return all events recorded for ``topic`` (helper for assertions)."""
        return [event for (t, event, _key) in self.published if t == topic]
