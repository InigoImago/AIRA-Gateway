"""A worker's own statement that it is making progress, and how that statement is read (`FRD-153`).

A process that serves no HTTP has nothing a healthcheck can ask. So it writes down what it would
answer: when it last made progress, how old that may grow before it is not healthy any more, and —
while it makes none — why. The reader needs nothing else, so one image check serves every role an
image runs (`aira_common.healthcheck`), and the reason it prints is the worker's own.

**Progress, not life.** A worker beats only where it did what it exists to do: the consumer applied
or skipped an event, or is idle in contact with its broker; the relay or the retention sweep
finished a run. A process that is up and retrying is not healthy, and a heartbeat written on a timer
of its own would say that it was.

Standard library only, and no other module of this package: the healthcheck imports this one every
fifteen seconds in every container.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeIs

#: Where the heartbeat is written and read unless `AIRA_HEARTBEAT_FILE` names another file. `/tmp`
#: stays writable under a read-only root filesystem when a tmpfs is mounted there.
DEFAULT_PATH = "/tmp/aira-heartbeat"
PATH_VARIABLE = "AIRA_HEARTBEAT_FILE"

#: A heartbeat is some sixty bytes. A file larger than this holds something else, and is read no
#: further than the first byte past it.
LARGEST_BYTES = 4096

#: The file is written at most this often. Progress can come thousands of times a second — a new
#: consumer group replays every compacted topic — and the reader judges in tens of seconds.
WRITE_EVERY_SECONDS = 1.0

#: How much of a reason the file keeps. The reason is a sentence, and a failed pass's message can be
#: long; cut, it still says what failed, and the file stays well inside :data:`LARGEST_BYTES`.
REASON_CHARACTERS = 300


class Log(Protocol):
    """The two lines a heartbeat says about itself, and the one about a file it could not write."""

    def info(self, event: str, **fields: Any) -> Any: ...

    def warning(self, event: str, **fields: Any) -> Any: ...


def path_from_environment() -> Path:
    """The heartbeat file of this container, as both its writer and its reader find it."""
    return Path(os.environ.get(PATH_VARIABLE) or DEFAULT_PATH)


class NotAHeartbeat(ValueError):
    """The file is there and can be read, and what it holds is not a heartbeat."""


@dataclass(frozen=True, slots=True)
class Reading:
    """What a heartbeat file says: whose it is, how old it may grow, when the worker started and
    last made progress — ``None`` before its first — and why it has made none since, if it said."""

    worker: str
    max_age_seconds: float
    started_at: float
    written_at: float | None
    reason: str | None = None


def parse(raw: bytes) -> Reading:
    """Read a heartbeat file's content, or raise :class:`NotAHeartbeat` saying what it lacks.

    **Anything else is never healthy.** A file that was cut short, was written by something else or
    names an age that cannot be compared with — `NaN`, infinity, a negative number — would
    otherwise be a way to report a stuck worker as a working one.
    """
    if len(raw) > LARGEST_BYTES:
        raise NotAHeartbeat(f"it holds more than {LARGEST_BYTES} bytes")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NotAHeartbeat("it is not JSON") from exc
    if not isinstance(document, dict):
        raise NotAHeartbeat("it is not a JSON object")
    worker = document.get("worker")
    max_age = document.get("max_age_seconds")
    started_at = document.get("started_at")
    written_at = document.get("written_at", False)
    reason = document.get("reason")
    if not isinstance(worker, str) or not worker:
        raise NotAHeartbeat("it names no worker")
    if not _finite(max_age) or max_age <= 0:
        raise NotAHeartbeat("it carries no age it may reach")
    if not _finite(started_at):
        raise NotAHeartbeat("it carries no time the worker started")
    if written_at is not None and not _finite(written_at):
        raise NotAHeartbeat("it carries no time of the last progress, nor says there was none")
    if reason is not None and not isinstance(reason, str):
        raise NotAHeartbeat("its reason is not a sentence")
    return Reading(
        worker=worker,
        max_age_seconds=float(max_age),
        started_at=float(started_at),
        written_at=None if written_at is None else float(written_at),
        reason=reason or None,
    )


def _finite(value: object) -> TypeIs[int | float]:
    """A number that can be compared with a clock — never a boolean, which JSON calls a number."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def judge(reading: Reading, now: float) -> tuple[bool, str]:
    """Whether the heartbeat is young enough at ``now``, and the sentence that says so.

    **No progress yet is not healthy**, however recent the start: the image's start period is what
    forgives a worker its first minute, and a sweep whose database was never there is not given the
    two hours its heartbeat may otherwise reach.

    **A heartbeat dated in the future counts as just written, up to its own age.** A clock set back
    makes every heartbeat look younger than it is: a stuck worker is then noticed later by the
    amount the clock moved, and a working one is never called stuck for it. A heartbeat further
    ahead than it may ever be old was not written by this clock, and is not healthy.
    """
    because = f" — {reading.reason}" if reading.reason else ""
    limit = reading.max_age_seconds
    if reading.written_at is None:
        started = max(now - reading.started_at, 0.0)
        return False, f"{reading.worker}: no progress since it started {started:.0f} s ago{because}"
    age = now - reading.written_at
    if age > limit:
        return False, (
            f"{reading.worker}: no progress for {age:.0f} s, longer than the {limit:.0f} s it may "
            f"take{because}"
        )
    if -age > limit:
        return False, f"{reading.worker}: its heartbeat is dated {-age:.0f} s ahead of this clock"
    return True, f"{reading.worker}: progress {max(age, 0.0):.0f} s ago, within {limit:.0f} s"


class Heartbeat:
    """What a worker writes when it makes progress and why it makes none, and says in its log when
    that changes.

    The log tells the story the health status tells: ``worker_healthy`` at the first progress and at
    the first after a silence longer than the maximum age, ``worker_unhealthy`` once, with the
    reason, when the worker notices it has been silent that long (:meth:`missed`).
    """

    def __init__(
        self,
        worker: str,
        max_age_seconds: float,
        *,
        log: Log,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not (math.isfinite(max_age_seconds) and max_age_seconds > 0):
            raise ValueError(f"a heartbeat needs a positive maximum age, not {max_age_seconds!r}")
        self.worker = worker
        self.max_age_seconds = float(max_age_seconds)
        self._log = log
        self._path = path or path_from_environment()
        self._clock = clock
        self._started_at = clock()
        # The worker's last progress — its start, until it makes any — and whether it has made any.
        self._since = self._started_at
        self._progressed = False
        # What the file says: when it was last written, and the reason it carries.
        self._written: float | None = None
        self._reason: str | None = None
        # What the log last said: nothing yet, "healthy" or "unhealthy".
        self._said: str | None = None
        self._write_failed = False

    @property
    def path(self) -> Path:
        return self._path

    def start(self) -> None:
        """Say that this run has made no progress yet, over what an earlier run left behind.

        A restarted container keeps its filesystem, and the previous run's heartbeat would speak for
        this one until it is older than its age.
        """
        self._write(self._clock())

    def beat(self) -> None:
        """The worker made progress: write the heartbeat, and say so if it had been silent."""
        now = self._clock()
        quiet = now - self._since
        if self._said != "healthy" or quiet > self.max_age_seconds:
            self._log.info(
                "worker_healthy",
                worker=self.worker,
                quiet_seconds=round(quiet, 1),
                max_age_seconds=self.max_age_seconds,
            )
            self._said = "healthy"
            self._written = None
        stale_file = not self._progressed or self._reason is not None
        self._since, self._progressed, self._reason = now, True, None
        if stale_file or self._written is None or now - self._written >= WRITE_EVERY_SECONDS:
            self._write(now)

    def missed(self, reason: str) -> None:
        """The worker could not make progress, for ``reason``: the file says so from now on.

        Said in the log once, when the silence has outgrown the maximum age — the moment the
        healthcheck turns — and not again until the worker has made progress since.
        """
        now = self._clock()
        quiet = now - self._since
        if quiet > self.max_age_seconds and self._said != "unhealthy":
            self._log.warning(
                "worker_unhealthy",
                worker=self.worker,
                reason=reason,
                quiet_seconds=round(quiet, 1),
                max_age_seconds=self.max_age_seconds,
            )
            self._said = "unhealthy"
        said = _cut(reason)
        if said != self._reason:
            self._reason = said
            self._write(now)

    def seconds_until_stale(self) -> float | None:
        """How long until the silence outgrows the maximum age; ``None`` once that has been said."""
        if self._said == "unhealthy":
            return None
        return self._since + self.max_age_seconds - self._clock()

    def _write(self, now: float) -> None:
        """Replace the file in one step, so a reader sees the old heartbeat or the new, never half.

        ``now`` is when it is written, which spaces the writes; the time of the last progress is
        what the file carries.

        **A heartbeat that cannot be written does not stop the worker**: the worker is doing its
        job, and its container then reads unhealthy, which is the right thing to report about a
        heartbeat nobody can read. Said once, until a write succeeds again.
        """
        import tempfile  # Only the writer needs it; the healthcheck imports this module too.

        content = json.dumps(
            {
                "worker": self.worker,
                "max_age_seconds": self.max_age_seconds,
                "started_at": self._started_at,
                "written_at": self._since if self._progressed else None,
                "reason": self._reason,
            }
        )
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                dir=self._path.parent, prefix=f".{self._path.name}."
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                # Readable by whoever runs the check in this container, as the file it replaces was.
                os.fchmod(handle.fileno(), 0o644)
                handle.write(content)
            os.replace(temporary, self._path)
            temporary = None
        except OSError as exc:
            if not self._write_failed:
                self._log.warning(
                    "heartbeat_not_written",
                    worker=self.worker,
                    path=str(self._path),
                    error=str(exc),
                )
                self._write_failed = True
            return
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
        self._written = now
        self._write_failed = False


def _cut(reason: str) -> str:
    """A reason as long as the file keeps one."""
    if len(reason) <= REASON_CHARACTERS:
        return reason
    return reason[: REASON_CHARACTERS - 1] + "…"
