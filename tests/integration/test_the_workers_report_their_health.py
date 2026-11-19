"""Every worker's container says what its process is doing, through the outages it exists for.

The consumer, the relay and the retention sweep ran with their healthcheck switched off — the
image's one check asked a `/healthz` they do not serve — and the relay and the sweep in shell loops
whose `|| true` swallowed every failure. Each now writes a heartbeat when it makes progress, and
the image's check reads it (`FRD-153`). The hermetic suite proves the parts against a fake broker
and a fake clock. Whether they follow a real outage is a question only the stack answers —
aiokafka keeps a consumer's assignment while its broker is gone, and only the fetches it stops
answering show it — so this file stops the stack's Kafka, then its PostgreSQL, and reads each
worker's check: the command Docker runs, run beside it, and Docker's own record of it.

Each test waits for everything its outage touched to answer again before it returns, whatever it
found.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.kafka import AiokafkaProducer, KafkaRecord
from aira_gateway.db.base import build_engine

from . import installation
from .conftest import MANAGEMENT_DB
from .test_config_distribution import _queue_outbox_event, _wait_for
from .test_what_a_database_restart_costs import RECOVERED_WITHIN, _the_stack_answers, _until

pytestmark = pytest.mark.integration

KAFKA = stack_addresses.container("kafka")
POSTGRES = stack_addresses.container("postgres")
CONSUMER = stack_addresses.container("gateway-consumer")
RELAY = stack_addresses.container("management-relay")
RETENTION = stack_addresses.container("gateway-retention")
#: Every container of the stack that is meant to keep running and is built from this repository.
LONG_RUNNING = tuple(
    stack_addresses.container(service)
    for service in (
        "gateway",
        "gateway-consumer",
        "gateway-retention",
        "management",
        "management-relay",
        "frontend",
    )
)

#: How long a worker may take to read unhealthy once its cause began: the consumer's broker silence
#: (30 s) and age (90 s), the relay's age (120 s), a look every 15 s, a pass in between — and room.
UNHEALTHY_WITHIN = 240.0
#: And Docker after it: its own five failed checks, fifteen seconds apart.
DOCKER_AGREES_WITHIN = 150.0
#: Docker runs the check every 15 s, and keeps what it printed.
LOGGED_WITHIN = 60.0
#: How long it may take to read healthy again once the cause is gone: Kafka's start, a rejoin, a
#: held event's next attempt — at most 30 s after the last — and a look.
HEALTHY_WITHIN = 180.0
#: A sweep beside the stack's own, with a pause and an age a test can wait out.
SWEEP_HEARTBEAT = "/tmp/aira-heartbeat-live-sweep"
SWEEP_PID = "/tmp/aira-live-sweep.pid"
SWEEP = ["--loop", "--interval", "2", "--max-age", "10"]

#: What the consumer may say while its broker is away: aiokafka keeps the assignment and stops
#: getting answers, or gives the assignment up while it looks for its group's coordinator.
BROKER_AWAY = ("its broker has not answered a fetch", "no partition is assigned to it")


def _docker(*argv: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed program, arguments of this file's own
        ["docker", *argv], capture_output=True, text=True, timeout=timeout
    )


def _check(container: str, heartbeat: str | None = None) -> tuple[bool, str]:
    """The image's healthcheck, run in ``container`` as Docker runs it."""
    moved = ["-e", f"AIRA_HEARTBEAT_FILE={heartbeat}"] if heartbeat else []
    done = _docker("exec", *moved, container, "python", "-m", "aira_common.healthcheck", timeout=30)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def _unhealthy(container: str, heartbeat: str | None = None) -> str | None:
    healthy, said = _check(container, heartbeat)
    return None if healthy else said


def _health(container: str) -> dict[str, Any]:
    done = _docker("inspect", "--format", "{{json .State.Health}}", container)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout) or {}


def _logged(container: str, start: str) -> str | None:
    """The latest output Docker kept from the check that begins with ``start``."""
    for entry in reversed(_health(container).get("Log") or []):
        if entry.get("ExitCode") == 1 and str(entry.get("Output", "")).startswith(start):
            return str(entry["Output"])
    return None


async def _eventually(probe: Callable[[], Any], timeout: float, what: str) -> Any:
    """Poll ``probe`` every few seconds until it returns something true, and return that."""
    deadline = asyncio.get_running_loop().time() + timeout
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        last = probe()
        if last:
            return last
        await asyncio.sleep(3.0)
    raise AssertionError(f"{what}: not within {timeout:.0f}s")


async def _making_progress(*containers: str) -> None:
    """Wait until each worker reads healthy before an outage is caused. One that has made no
    progress yet — a container just started — reads unhealthy with no outage at all, and a test
    that went ahead would be reading its start, not the outage."""
    for container in containers:
        await _eventually(
            lambda c=container: _check(c)[0], HEALTHY_WITHIN, f"{container} makes progress first"
        )


def _use_case(slug: str) -> dict[str, str]:
    return {"slug": slug, "name": "Health probe", "description": "", "processing_notes": ""}


async def _forget(engine: AsyncEngine, management: AsyncEngine, slug: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": slug})
    async with management.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox_outboxevent WHERE key = :key"), {"key": slug}
        )


async def test_every_long_running_container_reads_healthy() -> None:
    """With the shipped Compose files, and nothing in them about health."""
    for container in LONG_RUNNING:
        await _eventually(
            lambda c=container: _health(c).get("Status") == "healthy",
            180.0,
            f"{container} reads healthy",
        )
    for container, worker in ((CONSUMER, "consumer"), (RELAY, "relay"), (RETENTION, "retention")):
        healthy, said = _check(container)
        assert healthy and said.startswith(f"{worker}: progress "), (
            f"{container}'s health is its worker's heartbeat, not a server's: {said}"
        )


async def test_kafka_away_turns_the_consumer_and_a_relay_with_work_unhealthy_and_back(
    engine: AsyncEngine,
) -> None:
    slug = f"itest-health-{uuid.uuid4().hex[:8]}"
    installation.remember("read_model", slug)
    await _making_progress(CONSUMER, RELAY)
    management = build_engine(MANAGEMENT_DB)
    stopped = _docker("stop", KAFKA)
    assert stopped.returncode == 0, stopped.stderr
    try:
        # Work for the relay: with nothing pending it does not need the broker, and is healthy.
        await _queue_outbox_event(
            management, "aira.usecases", slug, "usecase.upserted", _use_case(slug)
        )

        consumer = await _eventually(
            lambda: _unhealthy(CONSUMER), UNHEALTHY_WITHIN, "the consumer reads unhealthy"
        )
        relay = await _eventually(
            lambda: _unhealthy(RELAY), UNHEALTHY_WITHIN, "the relay reads unhealthy"
        )
        assert consumer.startswith("consumer: no progress for "), consumer
        assert any(f" — {reason}" in consumer for reason in BROKER_AWAY), consumer
        assert relay.startswith("relay: no progress for "), relay
        assert " — its last pass failed: " in relay, relay

        # Docker runs the same check, keeps the same sentence, and after its own retries agrees.
        await _eventually(
            lambda: _logged(CONSUMER, "consumer: no progress for "),
            LOGGED_WITHIN,
            "Docker keeps the consumer's reason",
        )
        await _eventually(
            lambda: _logged(RELAY, "relay: no progress for "),
            LOGGED_WITHIN,
            "Docker keeps the relay's reason",
        )
        for container in (CONSUMER, RELAY):
            await _eventually(
                lambda c=container: _health(c).get("Status") == "unhealthy",
                DOCKER_AGREES_WITHIN,
                f"Docker marks {container} unhealthy",
            )
    finally:
        started = _docker("start", KAFKA)
        assert started.returncode == 0, started.stderr
        await _eventually(lambda: _health(KAFKA).get("Status") == "healthy", 180.0, "Kafka is back")

    try:
        for container in (CONSUMER, RELAY):
            await _eventually(
                lambda c=container: _check(c)[0], HEALTHY_WITHIN, f"{container} reads healthy again"
            )
            await _eventually(
                lambda c=container: _health(c).get("Status") == "healthy",
                LOGGED_WITHIN,
                f"Docker marks {container} healthy again",
            )
        row = await _wait_for(
            engine, "SELECT name FROM use_cases WHERE slug = :slug", {"slug": slug}, timeout=60.0
        )
        assert row is not None, "the event written while Kafka was away arrived once it was back"
    finally:
        await _forget(engine, management, slug)
        await management.dispose()


async def test_postgres_away_turns_the_relay_the_sweep_and_a_held_consumer_unhealthy_and_back(
    engine: AsyncEngine,
) -> None:
    """The stack's sweep pauses an hour and may be two hours old; a second one beside it, every two
    seconds and ten seconds old at most, is the same code on the same database."""
    slug = f"itest-health-{uuid.uuid4().hex[:8]}"
    installation.remember("read_model", slug)
    management = build_engine(MANAGEMENT_DB)
    launched = _docker(
        "exec",
        "-d",
        "-e",
        f"AIRA_HEARTBEAT_FILE={SWEEP_HEARTBEAT}",
        RETENTION,
        "sh",
        "-c",
        f'echo $$ > {SWEEP_PID} && exec timeout -s TERM 900 python -m aira_gateway.retention "$@"',
        "sweep",
        *SWEEP,
    )
    assert launched.returncode == 0, launched.stderr
    try:
        await _eventually(
            lambda: _check(RETENTION, SWEEP_HEARTBEAT)[0], 90.0, "the second sweep reads healthy"
        )
        await _making_progress(CONSUMER, RELAY)
        stopped = _docker("stop", POSTGRES)
        assert stopped.returncode == 0, stopped.stderr
        try:
            # Straight to the broker: the relay cannot read the outbox now, and the consumer is to
            # be handed an event its database cannot take.
            producer = AiokafkaProducer(stack_addresses.netloc("kafka"))
            await producer.start()
            try:
                await producer.send(
                    KafkaRecord("aira.usecases", slug, "usecase.upserted", _use_case(slug))
                )
            finally:
                await producer.stop()

            sweep = await _eventually(
                lambda: _unhealthy(RETENTION, SWEEP_HEARTBEAT),
                60.0,
                "the second sweep reads unhealthy",
            )
            consumer = await _eventually(
                lambda: _unhealthy(CONSUMER), UNHEALTHY_WITHIN, "the consumer reads unhealthy"
            )
            relay = await _eventually(
                lambda: _unhealthy(RELAY), UNHEALTHY_WITHIN, "the relay reads unhealthy"
            )
            assert sweep.startswith("retention: no progress for "), sweep
            assert " — its last pass failed: OperationalError: " in sweep, sweep
            assert consumer.startswith("consumer: no progress for "), consumer
            assert consumer.endswith(" — an event is being applied or held for retry"), consumer
            assert " — its last pass failed: " in relay, relay
            await _eventually(
                lambda: _logged(CONSUMER, "consumer: no progress for "),
                LOGGED_WITHIN,
                "Docker keeps the consumer's reason",
            )
            await _eventually(
                lambda: _logged(RELAY, "relay: no progress for "),
                LOGGED_WITHIN,
                "Docker keeps the relay's reason",
            )
        finally:
            started = _docker("start", POSTGRES)
            assert started.returncode == 0, started.stderr
            await _until(_the_stack_answers, RECOVERED_WITHIN, "the stack after its database")

        for container, heartbeat in ((RETENTION, SWEEP_HEARTBEAT), (CONSUMER, None), (RELAY, None)):
            await _eventually(
                lambda c=container, h=heartbeat: _check(c, h)[0],
                HEALTHY_WITHIN,
                f"{container} reads healthy again",
            )
        row = await _wait_for(
            engine, "SELECT name FROM use_cases WHERE slug = :slug", {"slug": slug}, timeout=60.0
        )
        assert row is not None, "the event held through the outage was applied after it"
    finally:
        _docker(
            "exec",
            RETENTION,
            "sh",
            "-c",
            f'kill -TERM "$(cat {SWEEP_PID})" 2>/dev/null; rm -f {SWEEP_PID} {SWEEP_HEARTBEAT}',
        )
        await _forget(engine, management, slug)
        await management.dispose()
