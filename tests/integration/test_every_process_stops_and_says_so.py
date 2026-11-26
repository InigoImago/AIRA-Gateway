"""Every process stops when Docker tells it to, says so, and writes nothing but JSON lines
(`FRD-631` FR-2, FR-4).

On Stage T `docker compose stop` ended the relay and the retention sweep with exit code 0 and the
configuration consumer with 137: as a container's first process it had no SIGTERM handler, so the
signal was never delivered, Docker waited its ten seconds and killed it. The hermetic suite proves
the stop against a fake broker; whether the real process, as PID 1 in its image, stops in time is a
question only the stack answers. So does "only JSON on stdout", which a library writing to stderr
behind the pipeline's back would break where no unit test looks.

Each test starts what it stopped again and waits for it to read healthy before it returns.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time

import pytest
import stack_addresses

pytestmark = pytest.mark.integration

#: The acceptance bound for a stop, well inside Docker's own ten seconds.
STOPPED_WITHIN = 5.0
HEALTHY_WITHIN = 180.0

WORKERS = {
    "gateway-consumer": "worker_stopped",
    "management-relay": "worker_stopped",
    "gateway-retention": "worker_stopped",
}
PYTHON_SERVICES = (
    "gateway",
    "gateway-consumer",
    "gateway-retention",
    "management",
    "management-relay",
)


def _docker(*argv: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed program, arguments of this file's own
        ["docker", *argv], capture_output=True, text=True, timeout=timeout, check=False
    )


def _health(container: str) -> str:
    answer = _docker("inspect", "--format", "{{.State.Health.Status}}", container)
    return answer.stdout.strip()


async def _healthy_again(container: str) -> None:
    deadline = time.monotonic() + HEALTHY_WITHIN
    while time.monotonic() < deadline:
        if _health(container) == "healthy":
            return
        await asyncio.sleep(2.0)
    raise AssertionError(f"{container} did not read healthy again within {HEALTHY_WITHIN:.0f}s")


def _event(line: str) -> object:
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed.get("event") if isinstance(parsed, dict) else None


def _lines_since(container: str, since: str) -> list[str]:
    logs = _docker("logs", "--since", since, container)
    return [line for line in (logs.stdout + logs.stderr).splitlines() if line.strip()]


@pytest.mark.parametrize("service", sorted(WORKERS))
async def test_a_worker_stops_in_time_with_exit_code_0_and_says_so(service: str) -> None:
    container = stack_addresses.container(service)
    assert _health(container) == "healthy", f"{container} must be healthy before it is stopped"
    since = _docker("inspect", "--format", "{{.State.StartedAt}}", container).stdout.strip()

    started = time.monotonic()
    stopped = _docker("stop", "--time", "10", container)
    took = time.monotonic() - started
    try:
        assert stopped.returncode == 0, stopped.stderr
        exit_code = _docker("inspect", "--format", "{{.State.ExitCode}}", container).stdout
        assert exit_code.strip() == "0", f"{container} ended with {exit_code.strip()}"
        assert took < STOPPED_WITHIN, f"{container} took {took:.1f}s to stop"
        events = [_event(line) for line in _lines_since(container, since)]
        assert WORKERS[service] in events, f"{container} did not say it stopped: {events[-5:]}"
    finally:
        _docker("start", container)
        await _healthy_again(container)


@pytest.mark.parametrize("service", PYTHON_SERVICES)
def test_a_python_service_writes_nothing_but_json_lines(service: str) -> None:
    """With `AIRA_LOG_JSON=true` every line of the container's output — a library's, the web
    server's, a warning's — is one JSON object."""
    container = stack_addresses.container(service)
    since = _docker("inspect", "--format", "{{.State.StartedAt}}", container).stdout.strip()

    lines = _lines_since(container, since)

    assert lines, f"{container} wrote nothing since it started"
    not_json = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            not_json.append(line)
            continue
        if not isinstance(parsed, dict) or "event" not in parsed:
            not_json.append(line)
    assert not not_json, f"{container} wrote lines that are not JSON events: {not_json[:5]}"
