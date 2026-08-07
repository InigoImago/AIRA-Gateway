"""Extensible seed contribution registry (FRD-002).

Each phase registers its own seed step via :func:`register`. The ``seed_demo`` command
runs them in ``(order, name)`` order. Every step is idempotent and returns a summary of
what it created, so re-running is safe and observable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# A summary the command prints, not a contract anything computes with. Mostly counts — but the
# showcase step also reports the demo API keys it re-derived, and squeezing those into an int
# would mean the seed cannot tell you the one thing you need to call the gateway with.
SeedResult = dict[str, object]
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
