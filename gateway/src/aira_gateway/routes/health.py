"""Liveness and readiness endpoints.

``/healthz`` reports process liveness. ``/readyz`` probes the dependencies the gateway
needs (Postgres, Kafka) and returns 503 until they are reachable.

Redis is reported but does **not** fail readiness (ADR-0008): rate limiting and budget
enforcement both degrade to a documented fallback without it, so taking the instance out of
service would turn a cache outage into an outage. It is still surfaced, because degraded
operation that nobody can see is indistinguishable from working.

Two different things are reported, deliberately. ``checks.counters`` is a probe — is the store
reachable *now*. ``fallbacks`` is what each feature last experienced on the request path, and
names what its fallback costs while it lasts. A probe can succeed while traffic is still being
served degraded, and a store can be unreachable with no feature having needed it yet.
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
    """Liveness probe: the process is up and serving.

    **Deliberately trivial, and it must stay that way.** No I/O of any kind. A liveness probe that
    checks a dependency restarts a healthy process when that dependency blinks, which is how a
    restart loop gets built out of a transient outage.
    """
    return {"status": "ok"}


@router.get("/version-info")
async def version_info(request: Request) -> dict[str, object]:
    """What is running here (`FRD-117` FR-1). Unauthenticated, like the predecessor's.

    Absent build metadata yields **nulls, not an error**: a development run has no build number
    and should still answer. It carries no configuration and no secret — a commit hash identifies
    the code, which is exactly what somebody correlating a bug report needs and nothing more.
    """
    settings: GatewaySettings = request.app.state.settings
    commit = settings.git_commit or ""
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "buildNumber": settings.build_number or None,
        "buildTime": settings.build_time or None,
        "git": {
            "commit": commit or None,
            "commitShort": commit[:7] or None,
            "branch": settings.git_branch or None,
        },
    }


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

    # What the features have actually *experienced*, which the probe above cannot tell you: a
    # store that answers a ping right now may still have refused the last hundred requests, and
    # one that is unreachable matters only insofar as some feature had to fall back.
    degradation = getattr(request.app.state, "degradation", None)
    fallbacks = degradation.features if degradation is not None else {}

    # Read from a **cached** background verdict, never probed inline (`FRD-117` §5.2). An inline
    # probe makes readiness as slow as the slowest upstream, so one degraded provider evicts pods
    # that were serving perfectly well — a health check that can take down a healthy service.
    probe = getattr(request.app.state, "upstream_probe", None)
    upstreams = probe.snapshot() if probe is not None else {}

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            # Degraded is not "not ready": the instance still serves, with the fallbacks in
            # ADR-0008 in force. An unreachable *upstream* is the same shape of answer — a gateway
            # that still refuses over-budget requests and serves reporting is not down, and
            # evicting it helps nobody (FR-3). Anything watching this should alert, not evacuate.
            "degraded": not counters_ok or bool(fallbacks) or bool(probe and probe.degraded),
            "fallbacks": fallbacks,
            "checks": checks,
            "upstreams": upstreams,
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
