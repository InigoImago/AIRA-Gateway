"""Liveness and readiness endpoints for the management backend.

Mirrors the gateway's health contract: ``/healthz`` for liveness and ``/readyz`` for readiness —
Postgres decides it, Kafka is reported and only degrades it.
"""

from __future__ import annotations

import asyncio

from django.http import HttpRequest, JsonResponse

from aira_common.health import CheckResult, any_reachable, check_tcp
from aira_management.config.runtime import get_settings


def healthz(_request: HttpRequest) -> JsonResponse:
    """Liveness probe: the process is up and serving."""
    return JsonResponse({"status": "ok"})


def readyz(_request: HttpRequest) -> JsonResponse:
    """Readiness probe: Postgres decides it; Kafka is reported and degrades it.

    Every change this service makes is written to Postgres, its outbox included, and the relay
    publishes it later (`FRD-204`). While Kafka is down, configuration waits in the outbox, and
    the console keeps working — so a bus outage is `degraded`, the gateway's word, and never
    takes the service out of rotation. A cluster is reachable while any of its brokers is. Kafka is
    asked only when a broker is configured: with none there is nothing to reach.
    """
    cfg = get_settings()
    brokers = cfg.kafka_brokers

    async def _run() -> tuple[CheckResult, CheckResult | None]:
        database = await check_tcp("postgres", cfg.postgres_host, cfg.postgres_port)
        if not brokers:
            return database, None
        members = await asyncio.gather(*(check_tcp("kafka", host, port) for host, port in brokers))
        return database, any_reachable("kafka", members)

    database, kafka = asyncio.run(_run())
    checks: dict[str, dict[str, object]] = {
        "postgres": {"ok": database.ok, "detail": database.detail},
        "kafka": (
            {"ok": True, "detail": "not configured", "required": False}
            if kafka is None
            else {"ok": kafka.ok, "detail": kafka.detail, "required": False}
        ),
    }
    return JsonResponse(
        {
            "status": "ready" if database.ok else "not_ready",
            "degraded": kafka is not None and not kafka.ok,
            "checks": checks,
        },
        status=200 if database.ok else 503,
    )
