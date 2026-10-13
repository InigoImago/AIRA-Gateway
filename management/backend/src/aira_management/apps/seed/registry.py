"""Extensible seed contribution registry (FRD-002).

Each phase registers its own seed step via :func:`register`. The ``seed_demo`` command
runs them in ``(order, name)`` order. Every step is idempotent and returns a summary of
what it created, so re-running is safe and observable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: A summary the command prints, not a contract anything computes with: mostly counts, but the
#: showcase step also reports the demo API keys it re-derived.
SeedResult = dict[str, object]
SeedFn = Callable[[bool], SeedResult]
#: The slugs of the use cases a contribution creates, asked when ``--fresh`` decides what to keep.
UseCasesFn = Callable[[], Iterable[str]]

_REGISTRY: dict[str, SeedContribution] = {}


def _none() -> Iterable[str]:
    return ()


@dataclass(frozen=True, slots=True)
class SeedContribution:
    name: str
    order: int
    run: SeedFn
    #: The use cases this contribution creates. ``--fresh`` deletes every use case no contribution
    #: declares, so a slug left out here is deleted — announced to the gateway as a retirement —
    #: and then recreated by this contribution in the same run. The gateway keeps a retired use
    #: case as a tombstone that no upsert revives, so the recreation would refuse every request.
    use_cases: UseCasesFn = _none


def register(name: str, order: int, *, use_cases: UseCasesFn = _none) -> Callable[[SeedFn], SeedFn]:
    """Decorator registering a seed contribution (idempotent by name)."""

    def decorator(fn: SeedFn) -> SeedFn:
        _REGISTRY[name] = SeedContribution(name=name, order=order, run=fn, use_cases=use_cases)
        return fn

    return decorator


def contributions() -> list[SeedContribution]:
    """Return registered contributions sorted by (order, name)."""
    return sorted(_REGISTRY.values(), key=lambda c: (c.order, c.name))


def seeded_use_cases() -> frozenset[str]:
    """Every use case any contribution creates: what a ``--fresh`` reset keeps, so that recreating
    one is a reset rather than a retirement."""
    return frozenset(slug for entry in _REGISTRY.values() for slug in entry.use_cases())
