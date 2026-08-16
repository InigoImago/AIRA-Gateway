"""Liveness and readiness endpoints.

``/healthz`` reports process liveness. ``/readyz`` probes the dependencies the gateway
needs (Postgres, Kafka) and returns 503 until they are reachable.

**The verdict is public, the diagnosis is not** (2026-08-08). The full body names the database
host, the Kafka host, every configured upstream and which fallbacks are currently in force —
a map of the deployment and its weak spot. A probe needs the status code; an operator presents
the credential they already have. Locally the whole body is served to everyone.

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
from aira_common.secrets import secrets_state
from aira_gateway.auth.dependencies import resolve_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.security import is_local

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


def _is_operator(principal: Principal) -> bool:
    """Whether this credential is an **operator's**, in this system's own vocabulary.

    Two, and no third:

    - an **incident role** — Global Administrator or IT Security (`INCIDENT_ROLES`), the same set
      that may stop traffic and ask whether a model is reachable (`api/incidents.py`). Deliberately
      not `is_oversight`: IT Steuerung is given every *figure* and no write anywhere (PRD §154),
      and a deployment's topology is not a figure.
    - the **unbound break-glass key** (`ADR-0015`), minted by an operator with database access for
      the moment the control plane is unavailable. That moment is exactly when somebody needs to
      read this body, and it is the one credential in the system that means "an operator". A key
      **bound** to a use case is the opposite — it is issued by Management to a team, and it is the
      weakest credential here.
    """
    if principal.may_act_on_incidents:
        return True
    return principal.method == "api_key" and not principal.use_cases


async def _may_see_detail(request: Request) -> bool:
    """Whether this caller gets the diagnosis as well as the verdict.

    `/readyz` must stay unauthenticated — a Kubernetes probe carries no credential, and a readiness
    endpoint that answers 401 is an endpoint that reports every pod as unhealthy. But the full body
    names the database host, the Kafka bootstrap host, every configured upstream and which
    fallbacks are in force: a map of the deployment, its dependencies and their current weak spot,
    served to anyone who can reach the port.

    So the **verdict** is public and the **diagnosis** is not. A probe reads `status` and the status
    code, which is all it has ever used; an operator debugging one presents the credential they
    already have. Locally everything is shown, because a laptop has no topology to protect and an
    endpoint that is less useful in development is one people stop looking at.

    **"An operator", not "anybody who authenticated".** This asked only `principal is not None`,
    while the paragraph above says *operator* — so a use-case-scoped API key, which is the weakest
    credential this system issues and belongs to whichever team asked for one, was handed the
    database host, the Kafka host, the full upstream list, the current fallback state and the names
    of every secret loaded (`secrets_state()`). The gate now asks the question the docstring always
    described; see :func:`_is_operator`.
    """
    settings: GatewaySettings = request.app.state.settings
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
    probe: UpstreamProbe | None = getattr(request.app.state, "upstream_probe", None)
    upstreams = probe.snapshot() if probe is not None else {}

    degraded = not counters_ok or bool(fallbacks) or bool(probe and probe.degraded)
    if not await _may_see_detail(request):
        # The verdict, and nothing that describes the deployment. `degraded` stays because it is
        # the answer, not a detail — a caller who cannot tell "up" from "up on its fallbacks" has
        # to guess, and guessing here means either evacuating a healthy instance or ignoring a
        # real one.
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "degraded": degraded},
        )

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            # Degraded is not "not ready": the instance still serves, with the fallbacks in
            # ADR-0008 in force. An unreachable *upstream* is the same shape of answer — a gateway
            # that still refuses over-budget requests and serves reporting is not down, and
            # evicting it helps nobody (FR-3). Anything watching this should alert, not evacuate.
            "degraded": degraded,
            "fallbacks": fallbacks,
            "checks": checks,
            "upstreams": upstreams,
            # **Where the credentials came from.**
            #
            # `FRD-116` built Vault reading and the compose stack never passed `VAULT_ADDR`, so
            # for three days every credential came from the environment while the feature was
            # marked done. Nothing anywhere said so, and that is why nobody noticed: an absent
            # secret store is indistinguishable from a present one when neither is reported.
            #
            # Names only, never values — the same rule the loader logs under. Behind the same
            # credential gate as the rest of this body: which secrets an installation holds is
            # not a fact for an unauthenticated prober.
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
