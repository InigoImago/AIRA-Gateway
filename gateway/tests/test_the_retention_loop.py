"""`python -m aira_gateway.retention --loop`: a finished sweep is a heartbeat, a failed one is not.

Stored prompts are personal data, and nothing removes them unless the sweep runs. The stack ran it
as `while true; do python -m aira_gateway.retention || true; sleep 3600; done`: a sweep that could
not reach its database failed every hour, the `|| true` swallowed it, and payloads past their
retention stayed while the container read "Up". The loop is the sweep's own now (`FRD-153`).
"""

from __future__ import annotations

import itertools
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from aira_common.heartbeat import PATH_VARIABLE, judge, parse
from aira_gateway import retention
from aira_gateway.retention import (
    DEFAULT_INTERVAL_SECONDS,
    PruneResult,
    SweepFailed,
    arguments,
    max_age_for,
    sweep_forever,
    sweep_once,
)


class Lines:
    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> Lines:
    recorded = Lines()
    monkeypatch.setattr(retention, "_log", recorded)
    return recorded


class Sweeps:
    """A prune that fails with the errors given for its runs, and clears two payloads otherwise."""

    def __init__(self, failing: dict[int, Exception] | None = None) -> None:
        self.failing = failing or {}
        self.runs = 0

    async def __call__(self) -> PruneResult:
        self.runs += 1
        if self.runs in self.failing:
            raise self.failing[self.runs]
        return PruneResult(payloads_cleared=2, rows_deleted=0)


def _bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs: int
) -> tuple[threading.Event, Any, list[bool | None], list[float]]:
    beat = tmp_path / "beat"
    monkeypatch.setenv(PATH_VARIABLE, str(beat))
    stop = threading.Event()
    counted = itertools.count(1)
    after_each: list[bool | None] = []
    pauses: list[float] = []

    def wait(seconds: float) -> bool:
        pauses.append(seconds)
        if not beat.exists():
            after_each.append(None)
        else:
            reading = parse(beat.read_bytes())
            written = reading.written_at
            after_each.append(False if written is None else judge(reading, written)[0])
        if next(counted) >= runs:
            stop.set()
        return stop.is_set()

    return stop, wait, after_each, pauses


def _refused(detail: str) -> OperationalError:
    return OperationalError(
        "UPDATE request_logs SET request_payload=%(p)s",
        {"p": None},
        Exception(f'connection to server at "postgres" failed{detail}'),
    )


def test_a_sweep_that_finished_is_a_heartbeat_and_one_that_failed_is_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: Lines,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop, wait, after_each, pauses = _bounded(tmp_path, monkeypatch, runs=3)
    sweeps = Sweeps({2: _refused(": Connection refused")})

    sweep_forever(sweeps, stop=stop, wait=wait)

    assert sweeps.runs == 3, "the loop went on after the failed sweep"
    assert after_each == [True, True, True], "the failed sweep wrote nothing, the next one did"
    [failed] = lines.of("worker_pass_failed")
    assert failed["worker"] == "retention" and failed["error_type"] == "SweepFailed"
    assert failed["error"].startswith("OperationalError: (builtins.Exception) connection to server")
    assert "exc_info" not in failed
    assert pauses == [DEFAULT_INTERVAL_SECONDS] * 3
    assert capsys.readouterr().out.count("[retention] payloads cleared: 2, rows deleted: 0") == 2
    assert json.loads((tmp_path / "beat").read_text())["max_age_seconds"] == max_age_for(
        DEFAULT_INTERVAL_SECONDS
    )


def test_a_sweep_that_never_finished_is_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: Lines
) -> None:
    stop, wait, after_each, _pauses = _bounded(tmp_path, monkeypatch, runs=2)

    sweep_forever(Sweeps({1: _refused(""), 2: _refused("")}), stop=stop, wait=wait)

    assert after_each == [False, False]


def test_what_a_failed_statement_was_about_stays_out_of_the_log(lines: Lines) -> None:
    """A database's `DETAIL` quotes the row it refused — here a stored prompt. The line saying the
    sweep failed names the error and the statement, and no value."""

    async def refused() -> PruneResult:
        raise IntegrityError(
            "UPDATE request_logs SET request_payload=%(p)s",
            {"p": None},
            Exception(
                'null value in column "x" violates not-null constraint\n'
                "DETAIL:  Failing row contains (7, my card number is 4111 1111 1111 1111)."
            ),
        )

    with pytest.raises(SweepFailed) as failed:
        sweep_once(refused)

    assert "violates not-null constraint" in str(failed.value)
    assert "4111" not in str(failed.value) and "DETAIL" not in str(failed.value)


def test_a_failure_that_is_not_the_databases_is_logged_with_its_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: Lines
) -> None:
    stop, wait, after_each, _pauses = _bounded(tmp_path, monkeypatch, runs=1)
    bug = KeyError("periods")

    sweep_forever(Sweeps({1: bug}), stop=stop, wait=wait)

    [failed] = lines.of("worker_pass_failed")
    assert failed["exc_info"] is bug
    assert after_each == [False]


def test_the_pause_and_the_age_follow_the_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: Lines
) -> None:
    stop, wait, _after, pauses = _bounded(tmp_path, monkeypatch, runs=1)

    sweep_forever(Sweeps(), interval=60, stop=stop, wait=wait)

    assert pauses == [60.0]
    assert json.loads((tmp_path / "beat").read_text())["max_age_seconds"] == 720.0


def test_the_age_can_be_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: Lines
) -> None:
    stop, wait, _after, _pauses = _bounded(tmp_path, monkeypatch, runs=1)

    sweep_forever(Sweeps(), interval=2, max_age=10, stop=stop, wait=wait)

    assert json.loads((tmp_path / "beat").read_text())["max_age_seconds"] == 10.0


def test_the_age_is_two_pauses_and_a_sweep() -> None:
    assert max_age_for(3600) == 7800.0
    assert max_age_for(60) == 720.0


def test_a_loop_that_cannot_work_is_refused_before_its_first_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: Lines
) -> None:
    monkeypatch.setenv(PATH_VARIABLE, str(tmp_path / "beat"))
    sweeps = Sweeps()

    with pytest.raises(ValueError, match="goes stale during every pause"):
        sweep_forever(sweeps, interval=60, max_age=30)

    assert sweeps.runs == 0


def test_the_command_line() -> None:
    once = arguments([])
    assert (once.loop, once.interval, once.max_age) == (False, None, None)

    looping = arguments(["--loop", "--interval", "5", "--max-age", "30"])
    assert (looping.loop, looping.interval, looping.max_age) == (True, 5.0, 30.0)


@pytest.mark.parametrize("given", [["--interval", "5"], ["--max-age", "30"]])
def test_a_pause_without_a_loop_is_refused(
    given: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as refused:
        arguments(given)

    assert refused.value.code == 2
    assert "apply to --loop" in capsys.readouterr().err
