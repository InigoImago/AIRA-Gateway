"""In-process change hook for use-case events (FRD-202).

Subscribers are notified on use-case/membership changes. In FRD-204 a Kafka publisher
subscribes here; for now it is the extension point (and lets tests observe changes).
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
