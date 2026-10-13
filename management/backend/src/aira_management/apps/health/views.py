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
    """Readiness probe: dependencies are reachable.

    Kafka is asked only when a broker is configured: with none, there is nothing to reach, and a
    probe of a default address would hold an installation without Kafka `not_ready` for good. It
    is then reported as such, the gateway's shape.
    """
    cfg = get_settings()
    kafka = cfg.kafka_host_port

    async def _run() -> list[CheckResult]:
        results = [await check_tcp("postgres", cfg.postgres_host, cfg.postgres_port)]
        if kafka is not None:
            results.append(await check_tcp("kafka", *kafka))
        return results

    results = asyncio.run(_run())
    ready = all(r.ok for r in results)
    checks: dict[str, dict[str, object]] = {
        r.name: {"ok": r.ok, "detail": r.detail} for r in results
    }
    if kafka is None:
        checks["kafka"] = {"ok": True, "detail": "not configured", "required": False}
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )
