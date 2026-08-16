"""A detection round that fails gives the window back instead of losing it.

`tick` evaluates everything that arrived since a watermark, and everything it does reads a
database: which scopes saw traffic, the applicable rules, each rule's evaluation, the write of any
events.

So a failure in between must not move the watermark. It used to be able to: the scopes were held
in a set that `tick` took away in its first statement, the loop caught the exception and logged a
warning, and **the traffic in those minutes was never evaluated by any rule**. The next tick saw
only what arrived after the failure.

That is a quiet way to lose exactly the thing detection exists for. `ADR-0014` makes detection
asynchronous, which is a promise that it happens *later* — not that it may silently not happen.
A thousand rate-limited requests in the minute a database hiccupped is precisely the shape a
detector is for, and it would have been the minute nobody looked at.

**The watermark replaced the set** when the evaluator was made correct across instances
(`FRD-127`), and it makes this property simpler rather than harder: there is nothing to merge back,
because nothing was taken away. A failed round leaves `_since` where it was and the next round
re-reads the same window — including whatever arrived during the failure, which the old set-merge
had to handle explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aira_gateway.anomalies.service import AnomalyService

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class _Boom:
    """A sessionmaker that fails the way a database does: when a round tries to use it."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise RuntimeError("the database went away")


@pytest.fixture
def service() -> AnomalyService:
    return AnomalyService(sessionmaker=_Boom(), enabled=True)


def test_the_window_is_kept_when_the_round_fails(service: AnomalyService) -> None:
    """The property, read off the service rather than inferred from a log line."""
    service._since = NOW - timedelta(minutes=5)

    with pytest.raises(RuntimeError):
        _run(service.tick(NOW))

    assert service._since == NOW - timedelta(minutes=5), (
        "a failed round moved the watermark past the window it was about to evaluate, so the "
        "traffic in those minutes is never seen by any rule — and the only trace is one warning"
    )


def test_traffic_arriving_during_the_failure_is_covered_too(service: AnomalyService) -> None:
    """A round takes a few hundred milliseconds against a real database and requests do not stop
    for it. The watermark is a *lower* bound, so anything that arrived while the round was failing
    is inside the next round's window by construction — where the set this replaced had to merge
    the new scopes back explicitly, and would have dropped them if it had assigned instead."""
    service._since = NOW - timedelta(minutes=5)

    with pytest.raises(RuntimeError):
        _run(service.tick(NOW))

    # The next round reads from the same lower bound, so a request at any point after it — during
    # the failure included — is still unevaluated rather than skipped.
    assert service._since < NOW


def test_the_window_is_kept_when_the_failure_is_inside_the_round() -> None:
    """The failure that actually exercises *where* the watermark is written.

    `_Boom` above raises when the sessionmaker is called — before the round has done anything, so
    it proves the watermark is not advanced on the way in but says nothing about the ordinary case:
    a round that opens its session, claims the tick, and then fails **during the evaluation**. That
    is what a database blink or an unreadable rule looks like, and it is the case where advancing
    the watermark first would silently lose the window.

    Found by the mutation harness: moving `self._since = moment` above the evaluation survived
    every other test here.
    """
    from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    _run(create_all(engine))
    service = AnomalyService(sessionmaker=build_sessionmaker(engine), enabled=True)
    service._since = NOW - timedelta(minutes=5)

    async def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("a rule the evaluator could not read")

    service._evaluate = _explode  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        _run(service.tick(NOW))

    assert service._since == NOW - timedelta(minutes=5), (
        "the round opened its session and then failed, and the watermark moved past the window it "
        "never evaluated"
    )


def test_a_successful_round_still_moves_the_window() -> None:
    """The other direction, so "keep it on failure" cannot become "never advance it".

    An assertion about something being kept is defended only by one that shows it is normally
    released — otherwise a service that never advanced its watermark would pass every case above
    while re-evaluating a window that grows for ever.
    """
    from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    _run(create_all(engine))
    service = AnomalyService(sessionmaker=build_sessionmaker(engine), enabled=True)
    service._since = NOW - timedelta(minutes=5)

    # A real round against a real (empty) schema: no traffic, so no findings, and the watermark
    # advances. A stand-in sessionmaker would have proved only that the stand-in was called.
    assert _run(service.tick(NOW)) == []
    assert service._since == NOW


def _run(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)
