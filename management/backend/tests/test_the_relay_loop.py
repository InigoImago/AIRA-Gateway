"""`manage.py relay --loop`: a finished run is a heartbeat, a failed run is none (`FRD-153`).

The stack ran `while true; do python manage.py relay || true; sleep 10; done`. A relay that could
not reach its broker or its database failed every run, the `|| true` swallowed it, and the
container stayed "Up" while the outbox grew. The loop is the command's own now, and its container's
health is what its runs did: finished — published, nothing pending, or the outbox left to the run
that holds it — or failed.
"""

from __future__ import annotations

import itertools
import json
import threading
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

import aira_management.apps.outbox.management.commands.relay as relay
import pytest
from aira_management.apps.outbox.models import OutboxEvent
from django.core.management import call_command
from django.core.management.base import CommandError

from aira_common import periodic
from aira_common.heartbeat import PATH_VARIABLE, judge, parse
from aira_common.kafka import USECASE_TOPIC, InMemoryProducer, KafkaRecord

pytestmark = pytest.mark.django_db


class Lines:
    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []

    def _note(self, event: str, **fields: Any) -> None:
        self.said.append((event, fields))

    info = warning = error = _note

    def of(self, event: str) -> list[dict[str, Any]]:
        return [fields for said, fields in self.said if said == event]


class Loop:
    """`relay --loop` with its pauses counted and its heartbeat read after every run."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runs: int) -> None:
        self.beat = tmp_path / "beat"
        self.after_each_run: list[bool | None] = []
        self.pauses: list[float] = []
        self.lines = Lines()
        monkeypatch.setenv(PATH_VARIABLE, str(self.beat))
        monkeypatch.setattr(relay, "_log", self.lines)
        real = periodic.run_every

        def bounded(interval: float, once: Any, **options: Any) -> None:
            stop = threading.Event()
            counted = itertools.count(1)

            def pause(seconds: float) -> bool:
                # Read at the start of each pause: after the loop has judged the run before it.
                self.pauses.append(seconds)
                self.after_each_run.append(self.healthy())
                if next(counted) >= runs:
                    stop.set()
                return stop.is_set()

            real(interval, once, stop=stop, wait=pause, **options)

        monkeypatch.setattr(relay, "run_every", bounded)

    def healthy(self) -> bool | None:
        """What the check says the moment the run ended; ``None`` while there is no file."""
        if not self.beat.exists():
            return None
        reading = parse(self.beat.read_bytes())
        if reading.written_at is None:
            return False
        return judge(reading, reading.written_at)[0]

    def max_age(self) -> float:
        return float(json.loads(self.beat.read_text())["max_age_seconds"])


def _pending(n: int = 2) -> list[OutboxEvent]:
    return [
        OutboxEvent.objects.create(
            topic=USECASE_TOPIC, key=f"uc-{i}", event_type="usecase.upserted", payload={"i": i}
        )
        for i in range(n)
    ]


class FailsAt(InMemoryProducer):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    async def send(self, record: KafkaRecord) -> None:
        if record.key == self.key:
            raise ConnectionError("broker went away")
        await super().send(record)


def test_a_run_that_published_is_a_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loop = Loop(monkeypatch, tmp_path, runs=1)
    _pending()
    broker = InMemoryProducer()
    monkeypatch.setattr(relay, "build_producer", lambda: broker)
    out = StringIO()

    call_command("relay", "--loop", stdout=out)

    assert len(broker.sent) == 2 and "published 2 events" in out.getvalue()
    assert loop.after_each_run == [True]
    assert loop.pauses == [relay.DEFAULT_INTERVAL_SECONDS]
    assert loop.max_age() == relay.LEAST_MAX_AGE_SECONDS


def test_a_run_with_nothing_pending_is_a_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loop = Loop(monkeypatch, tmp_path, runs=1)
    out = StringIO()

    call_command("relay", "--loop", stdout=out)

    assert "no pending events" in out.getvalue()
    assert loop.after_each_run == [True]


def test_a_run_that_left_the_outbox_to_another_is_a_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Another run holds the outbox, and is publishing it: this relay did all it should."""
    loop = Loop(monkeypatch, tmp_path, runs=1)

    @contextmanager
    def held():  # noqa: ANN202
        yield False

    monkeypatch.setattr(relay, "exclusive_run", held)
    out = StringIO()

    call_command("relay", "--loop", stdout=out)

    assert "another relay run holds the outbox" in out.getvalue()
    assert loop.after_each_run == [True]


def test_a_run_that_failed_is_none_and_the_loop_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**The case the `|| true` hid.** The run stopped at an event, said where, and the next run
    published it; between the two, the container's heartbeat said nothing had been done."""
    loop = Loop(monkeypatch, tmp_path, runs=2)
    first, second = _pending()
    brokers = iter([FailsAt(second.key), InMemoryProducer()])
    monkeypatch.setattr(relay, "build_producer", lambda: next(brokers))

    call_command("relay", "--loop", "--interval", "3", stdout=StringIO(), stderr=StringIO())

    assert loop.after_each_run == [False, True]
    [failed] = loop.lines.of("worker_pass_failed")
    assert failed["error_type"] == "CommandError" and "exc_info" not in failed
    assert f"publishing stopped at event {second.pk}" in failed["error"]
    assert not OutboxEvent.objects.pending().exists()


def test_a_database_that_does_not_answer_is_said_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from django.db import OperationalError

    loop = Loop(monkeypatch, tmp_path, runs=1)

    def unreachable(_now: Any) -> int:
        raise OperationalError('connection to server at "postgres" failed: Connection refused')

    monkeypatch.setattr(relay, "sweep", unreachable)

    call_command("relay", "--loop", stdout=StringIO())

    assert loop.after_each_run == [False]
    [failed] = loop.lines.of("worker_pass_failed")
    assert failed["error_type"] == "OperationalError" and "exc_info" not in failed


def test_each_run_starts_on_a_connection_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A connection the database closed fails every query on it. A process per run never kept
    one; a loop that kept it would fail every run after a database restart."""
    Loop(monkeypatch, tmp_path, runs=2)
    closed: list[int] = []

    class Connections:
        def close_all(self) -> None:
            closed.append(len(closed) + 1)

    monkeypatch.setattr(relay, "connections", Connections())
    monkeypatch.setattr(relay, "sweep", lambda _now: (_ for _ in ()).throw(CommandError("down")))

    call_command("relay", "--loop", stdout=StringIO())

    assert closed == [1, 2], "after the failed runs as after any other"


def test_the_pause_and_the_age_follow_the_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loop = Loop(monkeypatch, tmp_path, runs=1)

    call_command("relay", "--loop", "--interval", "60", stdout=StringIO())

    assert loop.pauses == [60.0]
    assert loop.max_age() == 300.0, "five pauses, once that is more than two minutes"


def test_the_age_can_be_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = Loop(monkeypatch, tmp_path, runs=1)

    call_command("relay", "--loop", "--interval", "2", "--max-age", "15", stdout=StringIO())

    assert loop.max_age() == 15.0


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        (["--interval", "5"], "apply to --loop"),
        (["--max-age", "60"], "apply to --loop"),
        (["--loop", "--interval", "0"], "positive number of seconds"),
        (["--loop", "--interval", "nan"], "positive"),
        (["--loop", "--interval", "30", "--max-age", "20"], "goes stale during every pause"),
        (["--loop", "--max-age", "-1"], "positive maximum age"),
    ],
)
def test_a_loop_that_cannot_work_is_refused_before_its_first_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arguments: list[str], said: str
) -> None:
    monkeypatch.setenv(PATH_VARIABLE, str(tmp_path / "beat"))
    ran: list[bool] = []
    monkeypatch.setattr(relay, "sweep", lambda _now: ran.append(True) or 0)

    with pytest.raises(CommandError, match=said):
        call_command("relay", *arguments, stdout=StringIO())

    assert ran == []


def test_the_age_is_five_pauses_and_never_under_two_minutes() -> None:
    assert relay.max_age_for(10) == 120.0
    assert relay.max_age_for(24) == 120.0
    assert relay.max_age_for(25) == 125.0
    assert relay.max_age_for(60) == 300.0
