"""Liveness and readiness endpoints for the management backend.

Mirrors the gateway's health contract: ``/healthz`` for liveness and ``/readyz`` for
dependency readiness (Postgres + Kafka reachability).
"""

from __future__ import annotations

import asyncio

from django.http import HttpRequest, JsonResponse

from aira_common.health import CheckResult, check_tcp
from aira_management.config.runtime import get_settings


def healthz(_request: HttpRequest) -> JsonResponse:
    """Liveness probe: the process is up and serving."""
    return JsonResponse({"status": "ok"})


def readyz(_request: HttpRequest) -> JsonResponse:
    """Readiness probe: dependencies are reachable."""
    cfg = get_settings()
    kafka_host, kafka_port = cfg.kafka_host_port

    async def _run() -> list[CheckResult]:
        return [
            await check_tcp("postgres", cfg.postgres_host, cfg.postgres_port),
            await check_tcp("kafka", kafka_host, kafka_port),
        ]

    results = asyncio.run(_run())
    ready = all(r.ok for r in results)
    return JsonResponse(
        {
            "status": "ready" if ready else "not_ready",
            "checks": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
        },
        status=200 if ready else 503,
    )
