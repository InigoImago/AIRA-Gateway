"""A worker's heartbeat: written whole, judged by its own age, and never mistaken for health.

The healthcheck of every AIRA image reads this file when there is one (`FRD-153`), so what is in it
decides whether a container is reported healthy, and the reason it gives is the worker's own. Three
ways it could lie, each held here: a reader catching a half-written file, a file that is not a
heartbeat read as one, and a worker that stopped making progress still reading as young.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from aira_common import heartbeat as module
from aira_common.heartbeat import (
    DEFAULT_PATH,
    LARGEST_BYTES,
    PATH_VARIABLE,
    REASON_CHARACTERS,
    Heartbeat,
    NotAHeartbeat,
    Reading,
    judge,
    parse,
    path_from_environment,
)

START = 1_000_000.0


class Lines:
    """Stands in for a worker's logger: what it said, by event."""

    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def events(self) -> list[str]:
        return [event for event, _fields in self.said]

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _beating(tmp_path: Path, *, max_age: float = 90.0) -> tuple[Heartbeat, Clock, Lines]:
    clock, lines = Clock(), Lines()
    beat = Heartbeat("consumer", max_age, log=lines, path=tmp_path / "beat", clock=clock)
    return beat, clock, lines


def _file(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "beat").read_text())


def _reads(tmp_path: Path, now: float) -> tuple[bool, str]:
    return judge(parse((tmp_path / "beat").read_bytes()), now)


# == what is written ===============================================================================


def test_a_beat_writes_who_it_is_how_old_it_may_grow_and_when(tmp_path: Path) -> None:
    beat, clock, _lines = _beating(tmp_path)
    clock.now += 4

    beat.beat()

    assert _file(tmp_path) == {
        "worker": "consumer",
        "max_age_seconds": 90.0,
        "started_at": START,
        "written_at": START + 4,
        "reason": None,
    }
    assert parse((tmp_path / "beat").read_bytes()) == Reading("consumer", 90.0, START, START + 4)


def test_a_beat_replaces_the_file_in_one_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader sees the heartbeat before or the heartbeat after, never one being written: the new
    one is written beside it and renamed over it, and until the rename the old one is whole."""
    beat, clock, _lines = _beating(tmp_path)
    beat.beat()
    before = (tmp_path / "beat").read_text()
    renames: list[tuple[str, str, str]] = []
    real_replace = os.replace

    def watched(source: str, target: os.PathLike[str] | str) -> None:
        renames.append((source, str(target), Path(target).read_text()))
        real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", watched)
    clock.now += 30
    beat.beat()

    [(source, target, at_the_rename)] = renames
    assert target == str(tmp_path / "beat") and source != target
    assert Path(source).parent == tmp_path, "a rename within one directory is one step"
    assert at_the_rename == before
    assert _file(tmp_path)["written_at"] == clock.now
    assert sorted(p.name for p in tmp_path.iterdir()) == ["beat"], "nothing is left beside it"


def test_progress_faster_than_a_second_is_written_once_a_second(tmp_path: Path) -> None:
    beat, clock, _lines = _beating(tmp_path)

    beat.beat()
    first = clock.now
    clock.now += 0.4
    beat.beat()
    assert _file(tmp_path)["written_at"] == first

    clock.now += 0.6
    beat.beat()
    assert _file(tmp_path)["written_at"] == clock.now


def test_a_heartbeat_that_cannot_be_written_does_not_stop_the_worker(tmp_path: Path) -> None:
    """The worker goes on doing its job; the container reads unhealthy, which is the truth about a
    heartbeat nobody can read. Said once, and once more after it was written again in between."""
    clock, lines = Clock(), Lines()
    beat = Heartbeat("relay", 120, log=lines, path=tmp_path / "missing" / "beat", clock=clock)

    beat.beat()
    clock.now += 5
    beat.beat()

    [said] = lines.of("heartbeat_not_written")
    assert said["path"] == str(tmp_path / "missing" / "beat") and said["worker"] == "relay"

    (tmp_path / "missing").mkdir()
    clock.now += 5
    beat.beat()
    assert (tmp_path / "missing" / "beat").exists()
    assert sorted(p.name for p in (tmp_path / "missing").iterdir()) == ["beat"]

    (tmp_path / "missing" / "beat").unlink()
    (tmp_path / "missing").rmdir()
    clock.now += 5
    beat.beat()
    assert len(lines.of("heartbeat_not_written")) == 2, "a failure after a success is said again"


def test_a_start_writes_over_what_an_earlier_run_left(tmp_path: Path) -> None:
    """A restarted container keeps its files: the last run's heartbeat would speak for this one
    until it aged out. This run says it has made no progress yet, which is not healthy."""
    (tmp_path / "beat").write_text(
        json.dumps(
            {"worker": "consumer", "max_age_seconds": 90, "started_at": 1, "written_at": START - 5}
        )
    )
    beat, clock, _lines = _beating(tmp_path)

    beat.start()

    assert _file(tmp_path)["written_at"] is None
    assert _reads(tmp_path, clock.now + 12) == (
        False,
        "consumer: no progress since it started 12 s ago",
    )


def test_a_start_that_cannot_write_is_said_and_does_not_stop_the_worker(tmp_path: Path) -> None:
    (tmp_path / "beat").mkdir()
    beat, _clock, lines = _beating(tmp_path)

    beat.start()

    assert lines.of("heartbeat_not_written")
    assert (tmp_path / "beat").is_dir()


def test_a_heartbeat_needs_a_positive_age(tmp_path: Path) -> None:
    for age in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="positive maximum age"):
            Heartbeat("relay", age, log=Lines(), path=tmp_path / "beat")


def test_the_file_is_where_the_environment_says(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PATH_VARIABLE, raising=False)
    assert path_from_environment() == Path(DEFAULT_PATH)
    monkeypatch.setenv(PATH_VARIABLE, "")
    assert path_from_environment() == Path(DEFAULT_PATH)
    monkeypatch.setenv(PATH_VARIABLE, "/run/aira/beat")
    assert path_from_environment() == Path("/run/aira/beat")
    assert Heartbeat("relay", 120, log=Lines()).path == Path("/run/aira/beat")


# == the reason ====================================================================================


def test_the_reason_a_worker_makes_no_progress_is_what_the_check_says(tmp_path: Path) -> None:
    beat, clock, _lines = _beating(tmp_path)
    beat.beat()
    progress = clock.now

    clock.now += 20
    beat.missed("waiting for Kafka")
    assert _file(tmp_path)["written_at"] == progress, "a reason is no progress"
    assert _reads(tmp_path, clock.now)[0], "within the age, a reason does not make it unhealthy"

    assert _reads(tmp_path, progress + 104) == (
        False,
        "consumer: no progress for 104 s, longer than the 90 s it may take — waiting for Kafka",
    )

    clock.now += 5
    beat.beat()
    assert _file(tmp_path)["reason"] is None, "progress clears it, written at once"


def test_a_reason_is_written_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beat, clock, _lines = _beating(tmp_path)
    beat.beat()
    writes: list[str | None] = []
    real_write = Heartbeat._write

    def counted(self: Heartbeat, now: float) -> None:
        real_write(self, now)
        writes.append(_file(tmp_path)["reason"])

    monkeypatch.setattr(Heartbeat, "_write", counted)
    for _ in range(3):
        clock.now += 15
        beat.missed("waiting for Kafka")
    clock.now += 15
    beat.missed("no partition is assigned to it")

    assert writes == ["waiting for Kafka", "no partition is assigned to it"]


def test_a_long_reason_is_cut_and_the_file_stays_a_heartbeat(tmp_path: Path) -> None:
    beat, _clock, _lines = _beating(tmp_path)

    beat.missed("its last pass failed: " + "publishing stopped " * 400)

    reason = _file(tmp_path)["reason"]
    assert len(reason) == REASON_CHARACTERS and reason.endswith("…")
    assert parse((tmp_path / "beat").read_bytes()).reason == reason


# == what the log says =============================================================================


def test_the_log_says_when_the_worker_turns_healthy_and_unhealthy(tmp_path: Path) -> None:
    beat, clock, lines = _beating(tmp_path)

    clock.now += 3
    beat.beat()
    assert lines.of("worker_healthy") == [
        {"worker": "consumer", "quiet_seconds": 3.0, "max_age_seconds": 90.0}
    ], "the first progress is said"

    clock.now += 50
    beat.missed("waiting for Kafka")
    assert lines.events() == ["worker_healthy"], "a silence within the age is no news"

    clock.now += 41
    beat.missed("waiting for Kafka")
    clock.now += 30
    beat.missed("waiting for Kafka")
    assert lines.of("worker_unhealthy") == [
        {
            "worker": "consumer",
            "reason": "waiting for Kafka",
            "quiet_seconds": 91.0,
            "max_age_seconds": 90.0,
        }
    ], "said once, at the moment the healthcheck turns"

    clock.now += 9
    beat.beat()
    beat.beat()
    assert lines.events() == ["worker_healthy", "worker_unhealthy", "worker_healthy"]
    assert lines.of("worker_healthy")[-1]["quiet_seconds"] == 130.0


def test_a_silence_nobody_reported_is_still_said_when_progress_returns(tmp_path: Path) -> None:
    """A pass that hung longer than the age reports nothing while it hangs; the healthcheck does.
    When it ends, the log still says how long the worker was silent."""
    beat, clock, lines = _beating(tmp_path)
    beat.beat()
    clock.now += 200

    beat.beat()

    assert [fields["quiet_seconds"] for fields in lines.of("worker_healthy")] == [0.0, 200.0]


def test_a_worker_that_never_made_progress_is_said_to_be_unhealthy(tmp_path: Path) -> None:
    beat, clock, lines = _beating(tmp_path)

    clock.now += 95
    beat.missed("waiting for Kafka")

    assert lines.of("worker_unhealthy")[0]["quiet_seconds"] == 95.0
    assert _reads(tmp_path, clock.now) == (
        False,
        "consumer: no progress since it started 95 s ago — waiting for Kafka",
    )


def test_how_long_until_the_heartbeat_is_stale(tmp_path: Path) -> None:
    beat, clock, _lines = _beating(tmp_path)
    beat.beat()

    clock.now += 30
    assert beat.seconds_until_stale() == 60.0
    clock.now += 70
    assert beat.seconds_until_stale() == -10.0
    beat.missed("stuck")
    assert beat.seconds_until_stale() is None, "once it has been said, there is nothing to wait for"


# == how it is read ================================================================================


def _reading(age: float, max_age: float = 90.0) -> tuple[Reading, float]:
    return Reading("consumer", max_age, 900.0, 1_000.0), 1_000.0 + age


def test_a_heartbeat_within_its_age_is_healthy() -> None:
    healthy, said = judge(*_reading(12.4))

    assert healthy
    assert said == "consumer: progress 12 s ago, within 90 s"


def test_a_heartbeat_older_than_its_own_age_is_not() -> None:
    assert judge(*_reading(90.0))[0], "the age itself is still within it"
    assert not judge(*_reading(90.001))[0]

    healthy, said = judge(*_reading(95.2))

    assert not healthy
    assert said == "consumer: no progress for 95 s, longer than the 90 s it may take"


def test_no_progress_yet_is_not_healthy_however_recent_the_start() -> None:
    """The image's start period forgives a worker its first minute. The heartbeat does not, or a
    sweep whose database was never there would read healthy for its two hours."""
    reading = Reading("retention", 7800.0, 1_000.0, None, "its last pass failed: refused")

    assert judge(reading, 1_003.0) == (
        False,
        "retention: no progress since it started 3 s ago — its last pass failed: refused",
    )


def test_a_heartbeat_dated_ahead_counts_as_new_up_to_its_own_age() -> None:
    """A clock set back makes every heartbeat younger. Within its age that is read as new — a
    working worker is never called stuck for it — and beyond it as not this clock's."""
    healthy, said = judge(*_reading(-30.0))
    assert healthy and said == "consumer: progress 0 s ago, within 90 s"

    healthy, said = judge(*_reading(-91.0))
    assert not healthy
    assert said == "consumer: its heartbeat is dated 91 s ahead of this clock"


def _document(**fields: Any) -> bytes:
    document = {"worker": "relay", "max_age_seconds": 90, "started_at": 1, "written_at": 5}
    document.update(fields)
    return json.dumps({k: v for k, v in document.items() if v != "absent"}).encode()


@pytest.mark.parametrize(
    ("raw", "lacks"),
    [
        (b"", "not JSON"),
        (b"\xff\xfe", "not JSON"),
        (b"ok", "not JSON"),
        (b"[1, 2]", "not a JSON object"),
        (_document(worker="absent"), "names no worker"),
        (_document(worker=""), "names no worker"),
        (_document(worker=7), "names no worker"),
        (_document(max_age_seconds="absent"), "no age it may reach"),
        (_document(max_age_seconds=0), "no age it may reach"),
        (_document(max_age_seconds=-90), "no age it may reach"),
        (_document(max_age_seconds=True), "no age it may reach"),
        (_document(started_at="absent"), "no time the worker started"),
        (_document(started_at="1"), "no time the worker started"),
        (_document(written_at="absent"), "no time of the last progress"),
        (_document(written_at="5"), "no time of the last progress"),
        (_document(written_at=True), "no time of the last progress"),
        (_document(reason=["waiting"]), "its reason is not a sentence"),
        (b'{"worker": "relay", "max_age_seconds": NaN, "started_at": 1, "written_at": 5}', "age"),
        (
            b'{"worker": "relay", "max_age_seconds": Infinity, "started_at": 1, "written_at": 5}',
            "age",
        ),
        (b'{"worker": "relay", "max_age_seconds": 9, "started_at": 1, "written_at": NaN}', "last"),
        (b" " * (LARGEST_BYTES + 1), f"more than {LARGEST_BYTES} bytes"),
    ],
)
def test_what_is_not_a_heartbeat_is_never_read_as_one(raw: bytes, lacks: str) -> None:
    with pytest.raises(NotAHeartbeat, match=lacks):
        parse(raw)


def test_whole_numbers_are_times_and_ages_and_null_is_no_progress_yet() -> None:
    assert parse(_document()) == Reading("relay", 90.0, 1.0, 5.0)
    assert parse(_document(written_at=None, reason="")) == Reading("relay", 90.0, 1.0, None)
