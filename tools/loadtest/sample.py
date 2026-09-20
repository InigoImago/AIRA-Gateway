"""What the machine was doing while the load ran (`FRD-136` FR-6).

Four things a latency percentile cannot say on its own: how much CPU each container actually
burned, which of them was the busy one, whether the audit writer fell behind far enough to start
writing on the request path, and how many database connections were in use. All are read from what
the machine already exposes — no instrumentation is added to the product for a measurement.

**CPU is read from the cgroup, not from `docker stats`, and the difference is the whole finding.**
`docker stats` reported the gateway at **21%** during a step in which the container's own
`cpu.stat` showed **0.91 cores** — a process pinned to one core, reported as idle. Every conclusion
drawn from the first number was wrong in the same direction: it said the gateway was waiting on
something when it was in fact out of CPU. `cpu.stat` is a counter the kernel increments; a
before-and-after difference over a known wall clock is not a sample and cannot be wrong about a
busy process.

`docker stats` is kept, in its **streaming** form, for the memory figure and for a shape over time.
The one-shot form takes about a second of wall clock per call and spends it on the machine under
test, which is a poor way to observe a machine under test.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Series:
    """One container's CPU and memory over a run."""

    cpu: list[float] = field(default_factory=list)
    memory: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        if not self.cpu:
            return {}
        return {
            "cpu_mean_pct": round(sum(self.cpu) / len(self.cpu), 1),
            "cpu_max_pct": round(max(self.cpu), 1),
            "mem_max_mb": round(max(self.memory), 1),
        }


def _number(value: str) -> float:
    return float(value.strip().rstrip("%")) if value.strip().rstrip("%") else 0.0


def _megabytes(usage: str) -> float:
    """`docker stats` writes '123.4MiB / 15.04GiB'; only the first half is this container's."""
    amount = usage.split("/")[0].strip()
    for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024.0), ("B", 1 / 1048576)):
        if amount.endswith(suffix):
            try:
                return float(amount[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    return 0.0


class Sampler:
    """Collects `docker stats` in the background for as long as it is open."""

    def __init__(self, containers: list[str]) -> None:
        self._containers = containers
        self._series: dict[str, Series] = {name: Series() for name in containers}
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Sampler:
        self._process = subprocess.Popen(
            [
                "docker",
                "stats",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
                *self._containers,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            # The streaming form redraws the screen, so each refresh arrives wrapped in terminal
            # escapes. Splitting on the tab the format string asked for is enough to ignore them.
            parts = line.replace("\x1b[2J", "").replace("\x1b[H", "").strip().split("\t")
            if len(parts) != 3:
                continue
            name = parts[0].strip()
            series = self._series.get(name)
            if series is None or parts[1].strip() in ("", "--"):
                continue
            series.cpu.append(_number(parts[1]))
            series.memory.append(_megabytes(parts[2]))

    def __exit__(self, *exc_info: object) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def summary(self) -> dict[str, dict[str, float]]:
        return {name: series.summary() for name, series in self._series.items() if series.summary()}


class Cpu:
    """CPU seconds a set of containers consumed between two moments, from their cgroups.

    Opened around a step and read after it. The reading is a subtraction of two counters, so it
    costs nothing while the step runs — which matters, because the observer shares the cores.
    """

    def __init__(self, containers: list[str]) -> None:
        self._paths = {name: _cgroup(name) for name in containers}
        self._before: dict[str, float] = {}
        self._wall = 0.0

    def __enter__(self) -> Cpu:
        self._before = {name: _cpu_seconds(path) for name, path in self._paths.items()}
        self._wall = time.monotonic()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._wall = time.monotonic() - self._wall
        self._after = {name: _cpu_seconds(path) for name, path in self._paths.items()}

    def summary(self, requests: int = 0) -> dict[str, dict[str, float]]:
        """``requests`` is what was **attempted**, not what succeeded.

        A refused request costs CPU too, and dividing by the successes alone turns a step that
        refused most of its traffic into a spectacular per-request figure: measured once at
        1 481 ms for an embedding batch, where 29.6 CPU-seconds were divided by the 20 requests
        that got through instead of the 2 453 that were made.
        """
        out: dict[str, dict[str, float]] = {}
        for name, before in self._before.items():
            after = getattr(self, "_after", {}).get(name)
            if after is None or before is None:
                continue
            seconds = after - before
            entry = {
                "cpu_seconds": round(seconds, 2),
                "cores": round(seconds / self._wall, 2) if self._wall else 0.0,
            }
            if requests:
                entry["cpu_ms_per_request"] = round(seconds * 1000 / requests, 2)
            out[name] = entry
        return out


def _cgroup(container: str) -> str:
    """Where this container's cgroup lives, or an empty string when it cannot be found."""
    try:
        identifier = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}}", container],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except subprocess.SubprocessError, OSError:
        return ""
    if not identifier:
        return ""
    root = Path("/sys/fs/cgroup")
    for candidate in root.rglob(f"*{identifier}*"):
        if candidate.is_dir() and (candidate / "cpu.stat").exists():
            return str(candidate)
    return ""


def _cpu_seconds(path: str) -> float | None:
    """`usage_usec` from a cgroup's `cpu.stat`, in seconds. ``None`` when unreadable — a missing
    reading is reported as missing rather than as zero, which would look like an idle container."""
    if not path:
        return None
    try:
        for line in Path(path, "cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1_000_000
    except OSError:
        return None
    return None


def gateway_warnings(container: str, since: str) -> dict[str, int]:
    """How often the gateway said something was wrong, by event name.

    `request_log_queue_full` is the one this run is looking for: it is the audit writer's own
    report that the queue is saturated and rows are being written on the request path
    (`persistence/writer.py`). There is no counter for it, and this — reading the log — is the gap
    `FRD-136` FR-6 records rather than papers over.
    """
    try:
        output = subprocess.run(
            ["docker", "logs", "--since", since, container],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.SubprocessError, OSError:
        return {}
    counts: dict[str, int] = {}
    for line in (output.stdout + output.stderr).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if str(entry.get("level", "")).lower() in ("warning", "error", "critical"):
            event = str(entry.get("event") or entry.get("message") or "?")[:80]
            counts[event] = counts.get(event, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1])[:10])


def postgres_connections(container: str, database: str, user: str = "aira") -> dict[str, Any]:
    """Backends in use, and how many are waiting. A pool that is the ceiling shows up here."""
    query = (
        "select count(*) total, count(*) filter (where state = 'active') active,"
        " count(*) filter (where wait_event_type = 'Lock') waiting"
        f" from pg_stat_activity where datname = '{database}'"
    )
    try:
        output = subprocess.run(
            ["docker", "exec", container, "psql", "-U", user, "-d", database, "-tAF,", "-c", query],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.SubprocessError, OSError:
        return {}
    parts = output.stdout.strip().split(",")
    if len(parts) != 3:
        return {}
    return {"total": int(parts[0]), "active": int(parts[1]), "waiting": int(parts[2])}


def now_stamp() -> str:
    """A `docker logs --since` argument for "from this moment"."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
