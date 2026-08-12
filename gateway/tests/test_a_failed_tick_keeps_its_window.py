"""A detection round that fails gives the window back instead of losing it.

`AnomalyService` is fed by the writer marking which scopes saw traffic, and `tick` takes that set
away in its first statement so a concurrent write cannot be missed. Everything after that reads a
database: the applicable rules, each rule's evaluation, the write of any events.

So a failure in between — a database blink, a rule the evaluator could not read — used to take the
window with it. The scopes were already cleared, the loop caught the exception and logged a
warning, and **the traffic in those minutes was never evaluated by any rule**. The next tick sees
only what arrived after the failure.

That is a quiet way to lose exactly the thing detection exists for. `ADR-0014` makes detection
asynchronous, which is a promise that it happens *later* — not that it may silently not happen.
A thousand rate-limited requests in the minute a database hiccupped is precisely the shape a
detector is for, and it would have been the minute nobody looked at.

The scopes are merged back rather than assigned, because traffic keeps arriving during the
failure and what it touched must not be dropped either. The set is bounded by the number of use
cases, so a persistent failure retries the same small set rather than growing one.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_gateway.anomalies.service import AnomalyService


class _Boom:
    """A sessionmaker that fails the way a database does: after the set has been taken."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise RuntimeError("the database went away")


@pytest.fixture
def service() -> AnomalyService:
    return AnomalyService(sessionmaker=_Boom(), enabled=True)


def test_the_scopes_are_kept_when_the_round_fails(service: AnomalyService) -> None:
    """The property, read off the service rather than inferred from a log line."""
    service.touch("kundenservice")
    service.touch("entwicklung")

    with pytest.raises(RuntimeError):
        _run(service.tick())

    assert service._touched == {"kundenservice", "entwicklung"}, (
        "a failed round dropped the window it was about to evaluate, so the traffic in those "
        "minutes is never seen by any rule — and the only trace is one warning"
    )


def test_traffic_arriving_during_the_failure_is_kept_too(service: AnomalyService) -> None:
    """Merged, not assigned. A round takes a few hundred milliseconds against a real database and
    requests do not stop for it, so restoring the *old* set over the new one would drop whatever
    arrived while the round was failing — the same defect, one window later."""
    service.touch("kundenservice")

    with pytest.raises(RuntimeError):
        _run(service.tick())
    service.touch("personalwesen")

    assert service._touched == {"kundenservice", "personalwesen"}


def test_a_successful_round_still_clears_the_window() -> None:
    """The other direction, so "keep them on failure" cannot become "never clear them".

    An assertion about something being kept is defended only by one that shows it is normally
    released — otherwise a service that never cleared its set would pass every case above while
    re-evaluating the same scopes for ever.
    """
    from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    _run(create_all(engine))
    service = AnomalyService(sessionmaker=build_sessionmaker(engine), enabled=True)
    service.touch("kundenservice")

    # A real round against a real (empty) schema: no rules, so no findings, and the window is
    # released. A stand-in sessionmaker would have proved only that the stand-in was called.
    assert _run(service.tick()) == []
    assert service._touched == set()


def _run(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)
