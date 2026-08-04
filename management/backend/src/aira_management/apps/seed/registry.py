"""Extensible seed contribution registry (FRD-002).

Each phase registers its own seed step via :func:`register`. The ``seed_demo`` command
runs them in ``(order, name)`` order. Every step is idempotent and returns a summary of
what it created, so re-running is safe and observable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

SeedResult = dict[str, int]
SeedFn = Callable[[bool], SeedResult]

_REGISTRY: dict[str, SeedContribution] = {}


@dataclass(frozen=True, slots=True)
class SeedContribution:
    name: str
    order: int
    run: SeedFn


def register(name: str, order: int) -> Callable[[SeedFn], SeedFn]:
    """Decorator registering a seed contribution (idempotent by name)."""

    def decorator(fn: SeedFn) -> SeedFn:
        _REGISTRY[name] = SeedContribution(name=name, order=order, run=fn)
        return fn

    return decorator


def contributions() -> list[SeedContribution]:
    """Return registered contributions sorted by (order, name)."""
    return sorted(_REGISTRY.values(), key=lambda c: (c.order, c.name))
