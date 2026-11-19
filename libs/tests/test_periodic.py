"""A worker's own loop: a failed pass is said and the loop goes on, only a pass that succeeded
beats, and SIGTERM ends it between passes (`FRD-153`).

The relay and the retention sweep ran in a shell loop whose `|| true` swallowed every failure: the
container stayed "Up" while nothing was published or deleted. The loop that replaces it is the
process's own, so these are the properties the container's health now rests on.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from aira_common.heartbeat import Heartbeat
from aira_common.periodic import STALE_MARGIN_SECONDS, run_every


class Lines:
    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def events(self) -> list[str]:
        return [event for event, _fields in self.said]

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


class Time:
    """A clock and a pause on it: each pause moves the clock on by what it waited."""

    def __init__(self, stop: threading.Event, *, pauses: int) -> None:
        self.now = 1_000_000.0
        self.stop = stop
        self.pauses_left = pauses
        self.waited: list[float] = []

    def clock(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waited.append(seconds)
        self.now += seconds
        self.pauses_left -= 1
        if self.pauses_left <= 0:
            self.stop.set()
        return self.stop.is_set()


class Passes:
    """A pass that fails on the runs named in ``failing`` and succeeds on every other."""

    def __init__(self, failing: dict[int, Exception] | None = None) -> None:
        self.failing = failing or {}
        self.runs = 0

    def __call__(self) -> None:
        self.runs += 1
        if self.runs in self.failing:
            raise self.failing[self.runs]


class Refused(Exception):
    """A failure whose message says what failed, as the relay's `CommandError` does."""


def _loop(
    tmp_path: Path, *, pauses: int, interval: float, max_age: float
) -> tuple[Heartbeat, Time, Lines, threading.Event]:
    stop = threading.Event()
    time_ = Time(stop, pauses=pauses)
    lines = Lines()
    beat = Heartbeat("relay", max_age, log=lines, path=tmp_path / "beat", clock=time_.clock)
    return beat, time_, lines, stop


def _written_at(tmp_path: Path) -> float | None:
    beat = tmp_path / "beat"
    return json.loads(beat.read_text())["written_at"] if beat.exists() else None


def test_a_pass_that_succeeds_is_a_heartbeat_and_one_that_fails_is_not(tmp_path: Path) -> None:
    beat, time_, lines, stop = _loop(tmp_path, pauses=3, interval=10, max_age=120)
    passes = Passes({2: Refused("publishing stopped at event 7")})
    written: list[float | None] = []

    def once() -> None:
        written.append(_written_at(tmp_path))
        passes()

    run_every(10, once, heartbeat=beat, log=lines, expected=(Refused,), stop=stop, wait=time_.wait)

    start = 1_000_000.0
    assert passes.runs == 3, "the loop went on after the failed pass"
    assert written == [None, start, start], "the failed second pass wrote nothing"
    assert _written_at(tmp_path) == start + 20
    assert lines.of("worker_pass_failed") == [
        {"worker": "relay", "error": "publishing stopped at event 7", "error_type": "Refused"}
    ]
    assert time_.waited == [10, 10, 10]
    assert lines.events()[-1] == "worker_stopped"


def test_an_unexpected_failure_is_logged_with_its_traceback(tmp_path: Path) -> None:
    beat, time_, lines, stop = _loop(tmp_path, pauses=1, interval=10, max_age=120)
    boom = KeyError("outbox")

    run_every(
        10,
        Passes({1: boom}),
        heartbeat=beat,
        log=lines,
        expected=(Refused,),
        stop=stop,
        wait=time_.wait,
    )

    [said] = lines.of("worker_pass_failed")
    assert said["exc_info"] is boom and said["error_type"] == "KeyError"


def test_a_heartbeat_an_earlier_run_left_is_written_over_before_the_first_pass(
    tmp_path: Path,
) -> None:
    """A restarted container keeps its files. Before this run's first pass, its heartbeat says it
    has made no progress yet; after a pass that failed, it says why."""
    (tmp_path / "beat").write_text(
        '{"worker": "relay", "max_age_seconds": 120, "started_at": 1, "written_at": 999999}'
    )
    beat, time_, lines, stop = _loop(tmp_path, pauses=1, interval=10, max_age=120)
    seen: list[dict[str, Any]] = []

    def once() -> None:
        seen.append(json.loads((tmp_path / "beat").read_text()))
        raise Refused("the broker is away")

    run_every(10, once, heartbeat=beat, log=lines, stop=stop, wait=time_.wait)

    assert seen[0]["written_at"] is None and seen[0]["reason"] is None
    after = json.loads((tmp_path / "beat").read_text())
    assert after["written_at"] is None
    assert after["reason"] == "its last pass failed: the broker is away"


def test_a_heartbeat_that_goes_stale_during_a_pause_is_said_when_it_does(tmp_path: Path) -> None:
    """The retention sweep pauses an hour. Its heartbeat may outgrow its age in the middle of one,
    and the log says so then, with the last failure as the reason — not an hour later."""
    beat, time_, lines, stop = _loop(tmp_path, pauses=4, interval=3600, max_age=7800)
    passes = Passes({2: Refused("database away"), 3: Refused("database away")})

    run_every(
        3600, passes, heartbeat=beat, log=lines, expected=(Refused,), stop=stop, wait=time_.wait
    )

    # Success at 0, failures at 3600 and 7200; the heartbeat is stale after 7800, 600 s into the
    # third pause, which is cut a moment after that to say so and then waits out the rest.
    assert time_.waited == [3600, 3600, 600 + STALE_MARGIN_SECONDS, 3000 - STALE_MARGIN_SECONDS]
    [said] = lines.of("worker_unhealthy")
    assert said["reason"] == "its last pass failed: database away"
    assert said["quiet_seconds"] == 7800.0 + STALE_MARGIN_SECONDS


def test_a_heartbeat_already_stale_when_a_pass_fails_is_said_once(tmp_path: Path) -> None:
    beat, time_, lines, stop = _loop(tmp_path, pauses=4, interval=10, max_age=15)
    passes = Passes({n: Refused("broker away") for n in range(1, 10)})

    run_every(
        10, passes, heartbeat=beat, log=lines, expected=(Refused,), stop=stop, wait=time_.wait
    )

    # Failures at 0 and 10: stale after 15, said 5 s into the second pause; the third pass at 20
    # fails and says nothing more.
    assert time_.waited == [10, 5 + STALE_MARGIN_SECONDS, 5 - STALE_MARGIN_SECONDS, 10]
    assert len(lines.of("worker_unhealthy")) == 1


def test_a_stop_during_the_early_wake_ends_the_pause(tmp_path: Path) -> None:
    beat, time_, lines, stop = _loop(tmp_path, pauses=2, interval=10, max_age=15)

    run_every(
        10,
        Passes({1: Refused("x"), 2: Refused("x")}),
        heartbeat=beat,
        log=lines,
        stop=stop,
        wait=time_.wait,
    )

    assert time_.waited == [10, 5 + STALE_MARGIN_SECONDS]
    assert lines.of("worker_unhealthy") == []


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_a_pause_is_a_positive_number_of_seconds(tmp_path: Path, interval: float) -> None:
    beat, _time, lines, _stop = _loop(tmp_path, pauses=1, interval=10, max_age=120)

    with pytest.raises(ValueError, match="positive number of seconds"):
        run_every(interval, Passes(), heartbeat=beat, log=lines)


def test_a_heartbeat_that_would_go_stale_in_every_pause_is_refused(tmp_path: Path) -> None:
    beat, _time, lines, _stop = _loop(tmp_path, pauses=1, interval=10, max_age=60)

    with pytest.raises(ValueError, match="goes stale during every pause of 60 s"):
        run_every(60, Passes(), heartbeat=beat, log=lines)


# == SIGTERM =======================================================================================


def _real_loop(tmp_path: Path) -> tuple[Heartbeat, Lines]:
    lines = Lines()
    return Heartbeat("retention", 600, log=lines, path=tmp_path / "beat"), lines


def test_sigterm_during_a_pass_lets_it_finish_and_starts_no_other(tmp_path: Path) -> None:
    beat, lines = _real_loop(tmp_path)
    finished: list[int] = []

    def once() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)  # the handler has run; the pass goes on regardless
        finished.append(len(finished) + 1)

    started = time.monotonic()
    run_every(300, once, heartbeat=beat, log=lines)

    assert finished == [1]
    assert time.monotonic() - started < 30, "the five-minute pause was not waited"
    assert lines.events() == ["worker_healthy", "worker_stopped"]


def test_sigterm_during_a_pause_ends_it(tmp_path: Path) -> None:
    beat, lines = _real_loop(tmp_path)
    passes = Passes()
    timer = threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGTERM))

    started = time.monotonic()
    timer.start()
    try:
        run_every(300, passes, heartbeat=beat, log=lines)
    finally:
        timer.cancel()

    assert passes.runs == 1
    assert time.monotonic() - started < 30


def test_the_previous_sigterm_handler_is_back_after_the_loop(tmp_path: Path) -> None:
    beat, lines = _real_loop(tmp_path)
    stop = threading.Event()
    stop.set()
    before = signal.getsignal(signal.SIGTERM)

    run_every(300, Passes(), heartbeat=beat, log=lines, stop=stop)

    assert signal.getsignal(signal.SIGTERM) is before


def test_off_the_main_thread_the_loop_leaves_signals_to_whoever_owns_them(tmp_path: Path) -> None:
    beat, lines = _real_loop(tmp_path)
    stop = threading.Event()
    passes = Passes()
    before = signal.getsignal(signal.SIGTERM)

    def once() -> None:
        passes()
        stop.set()

    worker = threading.Thread(
        target=run_every, args=(300, once), kwargs={"heartbeat": beat, "log": lines, "stop": stop}
    )
    worker.start()
    worker.join(timeout=30)

    assert not worker.is_alive() and passes.runs == 1
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_pause_that_ends_early_on_the_heartbeats_clock_still_says_it(tmp_path: Path) -> None:
    """The pause and the heartbeat keep different clocks. Woken before the heartbeat turned — on
    its clock — the loop waits a moment more and says it then, rather than at the next pass."""
    beat, time_, lines, stop = _loop(tmp_path, pauses=4, interval=10, max_age=15)
    exact = time_.wait
    early = iter([0.0, 0.0 - STALE_MARGIN_SECONDS - 0.3])

    def drifting(seconds: float) -> bool:
        time_.now += next(early, 0.0)
        return exact(seconds)

    run_every(
        10,
        Passes({n: Refused("broker away") for n in range(1, 10)}),
        heartbeat=beat,
        log=lines,
        expected=(Refused,),
        stop=stop,
        wait=drifting,
    )

    # The second pause ended 0.3 s short of the turn on the heartbeat's clock: one more wait, of
    # those 0.3 s and the margin, and then it is said.
    margin = STALE_MARGIN_SECONDS
    assert time_.waited[:3] == pytest.approx([10, 5 + margin, 0.3 + margin])
    [said] = lines.of("worker_unhealthy")
    assert said["quiet_seconds"] > 15


def test_a_heartbeat_that_turns_in_the_last_second_of_a_pause_is_said_before_the_next_pass(
    tmp_path: Path,
) -> None:
    """A pass that took 2.5 s of a 12 s age leaves 9.5 s of a 10 s pause: the wake a moment after
    the turn is the end of the pause, and the reason is said there rather than lost."""
    beat, time_, lines, stop = _loop(tmp_path, pauses=2, interval=10, max_age=12)

    def slow_failure() -> None:
        time_.now += 2.5
        raise Refused("broker away")

    run_every(
        10, slow_failure, heartbeat=beat, log=lines, expected=(Refused,), stop=stop, wait=time_.wait
    )

    assert time_.waited == [10, 10], "the whole first pause, then the second"
    [said] = lines.of("worker_unhealthy")
    assert said["quiet_seconds"] == 12.5, "said at the end of the first pause, not in the second"
