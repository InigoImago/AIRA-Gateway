"""Liveness and readiness endpoints.

``/healthz`` reports process liveness. ``/readyz`` probes the dependencies the gateway
needs (Postgres, Kafka) and returns 503 until they are reachable.

Redis is reported but does **not** fail readiness (ADR-0008): rate limiting and budget
enforcement both degrade to a documented fallback without it, so taking the instance out of
service would turn a cache outage into an outage. It is still surfaced, because degraded
operation that nobody can see is indistinguishable from working.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aira_common.counters import CountersUnavailable
from aira_common.health import check_tcp
from aira_gateway.config import GatewaySettings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe: the process is up and serving."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe: dependencies are reachable."""
    settings: GatewaySettings = request.app.state.settings
    kafka_host, kafka_port = settings.kafka_host_port

    results = [
        await check_tcp("postgres", settings.postgres_host, settings.postgres_port),
        await check_tcp("kafka", kafka_host, kafka_port),
    ]
    ready = all(r.ok for r in results)
    checks: dict[str, object] = {r.name: {"ok": r.ok, "detail": r.detail} for r in results}

    counters_ok, counters_detail = await _counters_state(request)
    checks["counters"] = {"ok": counters_ok, "detail": counters_detail, "required": False}

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            # Degraded is not "not ready": the instance still serves, with the fallbacks in
            # ADR-0008 in force. Anything watching this should alert, not evacuate.
            "degraded": not counters_ok,
            "checks": checks,
        },
    )


async def _counters_state(request: Request) -> tuple[bool, str]:
    """Whether the shared counter store answers, without failing readiness if it does not."""
    runner = getattr(request.app.state, "counters", None)
    if runner is None:
        return False, "not configured"
    try:
        await runner.run("return 1", [], [])
    except CountersUnavailable as exc:
        return False, f"unavailable — rate limits are per-instance, budgets racy ({exc})"
    return True, "reachable"
