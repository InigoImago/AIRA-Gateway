"""Liveness and readiness endpoints.

``/healthz`` reports process liveness. ``/readyz`` probes the dependencies the gateway needs
(Postgres, Kafka) and returns 503 until they are reachable.

**The verdict is public, the diagnosis is not.** A probe needs only the status code; the full body
maps the deployment and its weak spots, so it is served to operators (and to everyone locally).

Redis is reported but does **not** fail readiness (ADR-0008): rate limits and budgets degrade to a
documented fallback without it, and evicting the instance would turn a cache outage into an
outage. ``checks.counters`` is a probe of the store *now*; ``fallbacks`` is what each feature last
experienced on the request path — the two can disagree in either direction.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aira_common.counters import CountersUnavailable
from aira_common.health import check_tcp
from aira_common.secrets import secrets_state
from aira_gateway.auth.dependencies import resolve_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.security import is_local
from aira_gateway.state import settings_of

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe: the process is up and serving.

    **No I/O, and it must stay that way**: a liveness probe that checks a dependency restarts a
    healthy process when that dependency blinks.
    """
    return {"status": "ok"}


@router.get("/version-info")
async def version_info(request: Request) -> dict[str, object]:
    """What is running here (`FRD-117` FR-1). Unauthenticated, like the predecessor's.

    Absent build metadata yields nulls, not an error. No configuration and no secret — a commit hash
    identifies the code and nothing more.
    """
    settings = settings_of(request)
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


def _is_operator(principal: Principal) -> bool:
    """Whether this credential is an **operator's**. Two kinds, and no third:

    - an **incident role** — Global Administrator or IT Security (`INCIDENT_ROLES`). Not
      `is_oversight`: IT Steuerung gets every figure, and a deployment's topology is not a figure.
    - the **unbound break-glass key** (`ADR-0015`), minted for when the control plane is down —
      exactly when this body is needed. A key **bound** to a use case is a team's, and the weakest
      credential here.
    """
    if principal.may_act_on_incidents:
        return True
    return principal.method == "api_key" and not principal.use_cases


async def _may_see_detail(request: Request) -> bool:
    """Whether this caller gets the diagnosis as well as the verdict.

    `/readyz` stays unauthenticated — a Kubernetes probe carries no credential — but the full body
    names the database and Kafka hosts, every upstream, the fallbacks in force and the secrets
    loaded. An operator (:func:`_is_operator`) presents the credential they already have; locally
    everything is shown.
    """
    settings = settings_of(request)
    if is_local(settings):
        return True
    try:
        principal = await resolve_principal(request)
    except Exception:  # noqa: BLE001 - a broken credential must not break the readiness probe
        return False
    return principal is not None and _is_operator(principal)


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe: dependencies are reachable."""
    settings = settings_of(request)
    kafka_host, kafka_port = settings.kafka_host_port

    results = [
        await check_tcp("postgres", settings.postgres_host, settings.postgres_port),
        await check_tcp("kafka", kafka_host, kafka_port),
    ]
    ready = all(r.ok for r in results)
    checks: dict[str, object] = {r.name: {"ok": r.ok, "detail": r.detail} for r in results}

    counters_ok, counters_detail = await _counters_state(request)
    checks["counters"] = {"ok": counters_ok, "detail": counters_detail, "required": False}

    degradation = getattr(request.app.state, "degradation", None)
    fallbacks = degradation.features if degradation is not None else {}

    # A cached background verdict, never probed inline (`FRD-117` §5.2): an inline probe makes
    # readiness as slow as the slowest upstream and evicts healthy pods.
    probe: UpstreamProbe | None = getattr(request.app.state, "upstream_probe", None)
    upstreams = probe.snapshot() if probe is not None else {}

    degraded = not counters_ok or bool(fallbacks) or bool(probe and probe.degraded)
    if not await _may_see_detail(request):
        # `degraded` stays: it is the answer, not a detail describing the deployment.
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "degraded": degraded},
        )

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            # Degraded is not "not ready": the instance still serves on its fallbacks (ADR-0008,
            # FR-3). Anything watching this should alert, not evacuate.
            "degraded": degraded,
            "fallbacks": fallbacks,
            "checks": checks,
            "upstreams": upstreams,
            # Where the credentials came from (`FRD-116`) — names only, never values.
            "secrets": secrets_state(),
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
