"""A worker that runs a pass now and another after every pause, until told to stop (`FRD-153`).

The relay and the retention sweep each do one pass and exit. Run in a shell loop —
``while true; do … || true; sleep …; done`` — every failure was swallowed and the container stayed
"Up" while nothing was published or deleted. The loop is the process's own instead:

- **a pass that fails is said and the loop goes on**, and only a pass that succeeded is a heartbeat
  (:mod:`aira_common.heartbeat`), so the container's health is the passes' outcome;
- **SIGTERM ends it between passes**: a pass that has begun finishes, the pause does not;
- **a heartbeat that goes stale during a pause is said when it does**, not when the next pass
  starts — an hour later, for the retention sweep.
"""

from __future__ import annotations

import contextlib
import math
import signal
import threading
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from aira_common.heartbeat import Heartbeat
from aira_common.logging.exceptions import loggable

#: How long after its heartbeat turns stale a pausing worker wakes to say so.
STALE_MARGIN_SECONDS = 1.0


class Log(Protocol):
    def info(self, event: str, **fields: Any) -> Any: ...

    def error(self, event: str, **fields: Any) -> Any: ...


#: Waits up to the given seconds; returns ``True`` if it was ended by the stop instead.
Wait = Callable[[float], bool]


def run_every(
    interval_seconds: float,
    once: Callable[[], object],
    *,
    heartbeat: Heartbeat,
    log: Log,
    expected: tuple[type[Exception], ...] = (),
    stop: threading.Event | None = None,
    wait: Wait | None = None,
) -> None:
    """Run ``once`` now and again ``interval_seconds`` after each pass ends, until SIGTERM.

    A pass that raises one of ``expected`` is logged by its message, which says what failed; any
    other exception is logged with its traceback, since nobody wrote that message.
    """
    if not (math.isfinite(interval_seconds) and interval_seconds > 0):
        raise ValueError(f"a pause must be a positive number of seconds, not {interval_seconds!r}")
    if heartbeat.max_age_seconds <= interval_seconds:
        # Then every pause, after every pass that succeeded, would read as a stuck worker.
        raise ValueError(
            f"a heartbeat that may be {heartbeat.max_age_seconds:g} s old goes stale during every "
            f"pause of {interval_seconds:g} s"
        )
    stop = stop or threading.Event()
    pause = wait or stop.wait
    heartbeat.start()
    with _stopped_by_sigterm(stop):
        while not stop.is_set():
            reason = _one_pass(once, heartbeat, log, expected)
            _rest(interval_seconds, heartbeat, reason, pause)
    log.info("worker_stopped", worker=heartbeat.worker)


def _one_pass(
    once: Callable[[], object],
    heartbeat: Heartbeat,
    log: Log,
    expected: tuple[type[Exception], ...],
) -> str:
    """Run one pass. Returns what to say if the heartbeat goes stale before the next one."""
    try:
        once()
    except Exception as exc:  # noqa: BLE001 - a failed pass is reported and the loop goes on
        described = loggable(exc) or type(exc).__name__
        if isinstance(exc, expected):
            log.error(
                "worker_pass_failed",
                worker=heartbeat.worker,
                error=described,
                error_type=type(exc).__name__,
            )
        else:
            # Nobody wrote this message, so the line says where and what kind; the message and
            # the traceback are the log's at `DEBUG` only (`FRD-631` FR-8).
            log.error("worker_pass_failed", worker=heartbeat.worker, exc_info=exc)
        reason = f"its last pass failed: {described}"
        heartbeat.missed(reason)
        return reason
    heartbeat.beat()
    return "its last pass succeeded, and no pass has run since"


def _rest(seconds: float, heartbeat: Heartbeat, reason: str, pause: Wait) -> None:
    """Pause before the next pass — waking to say so if the heartbeat goes stale meanwhile.

    Woken a moment **after** it turns, and again until it has been said: the pause runs on one clock
    and the heartbeat on another, and a wake a millisecond early would otherwise say nothing until
    the next pass.
    """
    remaining = seconds
    while remaining > 0:
        stale_in = heartbeat.seconds_until_stale()
        if stale_in is None or stale_in >= remaining:
            pause(remaining)
            return
        step = min(max(stale_in, 0.0) + STALE_MARGIN_SECONDS, remaining)
        if pause(step):
            return
        remaining -= step
        heartbeat.missed(reason)


@contextlib.contextmanager
def _stopped_by_sigterm(stop: threading.Event) -> Iterator[None]:
    """SIGTERM sets ``stop`` for as long as the loop runs, and the previous handler returns after.

    **Without a handler the loop would not stop at all** as a container's first process: the kernel
    delivers no signal to PID 1 that it has left at its default, so ``docker stop`` waited its ten
    seconds and killed the pass wherever it was.
    """

    def _stop(_signum: int, _frame: object) -> None:
        stop.set()

    try:
        previous = signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        # Not the main thread: whoever started this thread owns the process's signals.
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
