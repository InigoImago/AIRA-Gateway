"""Every AIRA container reports its own health, and no compose file switches that off (`FRD-153`).

The three workers — the gateway's configuration consumer, the outbox relay and the retention sweep
— ran with `healthcheck: disable: true`, because the image's one check asked an HTTP endpoint they
do not serve, and the relay and the sweep ran in shell loops whose `|| true` swallowed every
failure. An operator saw nothing about whether configuration was applied or prompts were deleted.
The image's check reads the heartbeat a worker keeps now, which makes four rules checkable:

1. no long-running service of these files switches its healthcheck off;
2. no command swallows a failure with `|| true`;
3. a long-running service that runs a process of an AIRA image other than its server runs one that
   keeps a heartbeat — or checks itself, as the load test's model simulator does;
4. both Python images check with the one command, which asks their own port when there is no
   heartbeat.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from compose_files import COMPOSE_DIR, load

ROOT = Path(__file__).resolve().parents[2]

#: Every compose file this repository ships, overlays and the load test included: a file this
#: does not read is one where a worker could be switched off again unseen.
COMPOSE_FILES = sorted(COMPOSE_DIR.glob("docker-compose*.yml"))

#: The restart policies of a container that is meant to keep running.
LONG_RUNNING = {"always", "unless-stopped", "on-failure"}

#: The images whose processes this repository builds.
WORKER_IMAGES = ("aira-gateway", "aira-management")

#: The processes that keep a heartbeat, by the start of the command line that runs them, and the
#: source that keeps it — checked below, so the list cannot name a process that keeps none.
KEEPS_A_HEARTBEAT = {
    ("python", "-m", "aira_gateway.consumer.worker"): "gateway/src/aira_gateway/consumer/worker.py",
    (
        "python",
        "manage.py",
        "relay",
        "--loop",
    ): "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
    ("python", "-m", "aira_gateway.retention", "--loop"): "gateway/src/aira_gateway/retention.py",
}

DOCKERFILES = {
    ROOT / "gateway" / "Dockerfile": 8001,
    ROOT / "management" / "backend" / "Dockerfile": 8002,
}


def _services() -> list[tuple[str, str, dict[str, Any]]]:
    """Every service entry of every file, as ``(file, name, entry)`` — overlays included."""
    found = []
    for path in COMPOSE_FILES:
        for name, entry in ((load(path) or {}).get("services") or {}).items():
            found.append((path.name, name, entry or {}))
    return found


def _argv(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(shlex.split(value))
    return tuple(str(part) for part in value)  # type: ignore[attr-defined]


def _long_running() -> set[str]:
    return {name for _file, name, entry in _services() if entry.get("restart") in LONG_RUNNING}


def test_the_files_are_read() -> None:
    """A guard on the guards: a moved directory would make every rule below vacuous."""
    names = {path.name for path in COMPOSE_FILES}
    assert {"docker-compose.apps.yml", "docker-compose.yml", "docker-compose.showcase.yml"} <= names
    assert {"gateway-consumer", "management-relay", "gateway-retention"} <= _long_running()


def test_no_long_running_service_switches_its_healthcheck_off() -> None:
    """In the file that defines the service or in an overlay that changes it."""
    long_running = _long_running()
    switched_off = sorted(
        f"{file}: {name}"
        for file, name, entry in _services()
        if name in long_running
        and (
            (entry.get("healthcheck") or {}).get("disable") is True
            or (entry.get("healthcheck") or {}).get("test") in (["NONE"], "NONE")
        )
    )

    assert not switched_off, (
        f"These services switch their healthcheck off: {switched_off}. The image's check reads a "
        "worker's heartbeat; switched off, an operator sees nothing about whether it does its job."
    )


def test_no_command_swallows_a_failure() -> None:
    swallowed = sorted(
        f"{file}: {name}"
        for file, name, entry in _services()
        for key in ("command", "entrypoint")
        if "|| true" in " ".join(_argv(entry.get(key)))
    )

    assert not swallowed, (
        f"These commands carry on past a failure with `|| true`: {swallowed}. The container stays "
        "up and healthy-looking while the work fails; a loop belongs in the process, which says "
        "what failed and stops beating."
    )


def test_a_worker_of_an_aira_image_keeps_a_heartbeat() -> None:
    """A long-running AIRA process that is not the image's own server either keeps a heartbeat or
    brings its own check; otherwise the image's check asks a `/healthz` nobody serves."""
    unchecked = []
    for file, name, entry in _services():
        image = str(entry.get("image") or "")
        if not any(worker in image for worker in WORKER_IMAGES):
            continue
        if entry.get("restart") not in LONG_RUNNING or entry.get("command") is None:
            continue
        if (entry.get("healthcheck") or {}).get("test"):
            continue
        argv = _argv(entry.get("command"))
        if not any(argv[: len(start)] == start for start in KEEPS_A_HEARTBEAT):
            unchecked.append(f"{file}: {name} runs {' '.join(argv)}")

    assert not unchecked, (
        f"{unchecked}: a process of an AIRA image that serves no `/healthz` and keeps no "
        "heartbeat. Run its `--loop` form, which keeps one, or give the service a check of its own."
    )


@pytest.mark.parametrize(("command", "source"), KEEPS_A_HEARTBEAT.items())
def test_each_process_named_as_keeping_a_heartbeat_keeps_one(
    command: tuple[str, ...], source: str
) -> None:
    assert re.search(r"\bHeartbeat\(", (ROOT / source).read_text()), (
        f"{' '.join(command)} is named as keeping a heartbeat, and {source} writes none."
    )


@pytest.mark.parametrize(("dockerfile", "port"), DOCKERFILES.items(), ids=lambda v: str(v))
def test_both_python_images_check_with_the_one_command(dockerfile: Path, port: int) -> None:
    text = dockerfile.read_text()
    [healthcheck] = re.findall(r"^HEALTHCHECK [^\n]*\\\n\s*CMD (.+)$", text, re.MULTILINE)

    assert healthcheck == '["python", "-m", "aira_common.healthcheck"]'
    assert f"AIRA_HEALTH_PORT={port}" in text, "without a heartbeat, the check asks this port"
    assert f"EXPOSE {port}" in text and f'"--port", "{port}"' in text
