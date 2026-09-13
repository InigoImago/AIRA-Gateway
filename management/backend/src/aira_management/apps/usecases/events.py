"""In-process hook for use-case events (`FRD-202`).

Every configuration change is announced here; the outbox subscribes and carries each event to the
gateway over Kafka (`FRD-204`), and tests subscribe to observe changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Subscriber = Callable[[str, dict[str, Any]], None]

_subscribers: list[Subscriber] = []


def subscribe(subscriber: Subscriber) -> Subscriber:
    _subscribers.append(subscriber)
    return subscriber


def unsubscribe(subscriber: Subscriber) -> None:
    if subscriber in _subscribers:
        _subscribers.remove(subscriber)


def emit(event_type: str, payload: dict[str, Any]) -> None:
    for subscriber in list(_subscribers):
        subscriber(event_type, payload)
