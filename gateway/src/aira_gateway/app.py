"""FastAPI application factory for the Gateway API.

`create_app` builds an isolated instance with no import-time side effects, so tests can construct
apps with their own settings. The production entry point is `main`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, MutableMapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aira_common.counters import DegradationLog, ScriptRunner, build_runner
from aira_common.logging import get_logger
from aira_common.observability import redact_query_string
from aira_gateway import __version__
from aira_gateway.anomalies import AnomalyService
from aira_gateway.anomalies.suspensions import SuspensionService
from aira_gateway.api.gemini.routes import router as gemini_router
from aira_gateway.api.incidents import router as incidents_router
from aira_gateway.api.kira.info_routes import router as kira_info_router
from aira_gateway.api.kira.routes import router as kira_router
from aira_gateway.api.pipeline import router as pipeline_router
from aira_gateway.api.providers import router as providers_router
from aira_gateway.api.ranking.routes import router as ranking_router
from aira_gateway.api.reporting import router as reporting_router
from aira_gateway.api.usage import router as usage_router
from aira_gateway.auth.dependencies import require_attribution
from aira_gateway.auth.grants import GroupGrantResolver
from aira_gateway.auth.keys import DEMO_KEY_PREFIX
from aira_gateway.auth.oidc import build_oidc_validator
from aira_gateway.auth.role_definitions import RoleResolver
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.catalog import ModelCatalog
from aira_gateway.config import GatewaySettings, configure_process
from aira_gateway.cors import CorsMisconfigured, configure_cors
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.exception_handlers import register_exception_handlers
from aira_gateway.middleware import (
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    TraceIdMiddleware,
    UseCasePathMiddleware,
)
from aira_gateway.persistence.redaction import build_redactor
from aira_gateway.persistence.writer import RequestLogWriter
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.pipeline.store import PipelineStore
from aira_gateway.pricing import PricingService
from aira_gateway.ratelimit.buckets import (
    FallbackTokenBucket,
    InMemoryTokenBucket,
    RedisTokenBucket,
)
from aira_gateway.ratelimit.service import RateLimitService
from aira_gateway.reporting.service import ReportingService
from aira_gateway.routes.health import router as health_router
from aira_gateway.security import enforce_safe_settings, is_local_environment, is_local_or_demo
from aira_gateway.telemetry import instrument_outgoing_calls, redact_span_urls
from aira_gateway.upstreams.base import ProviderRegistry, Upstream
from aira_gateway.upstreams.foundry import build_foundry_upstreams
from aira_gateway.upstreams.gemini import build_gemini_upstream
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai import build_openai_upstreams
from aira_gateway.upstreams.vertex import build_vertex_upstreams

__all__ = ["CorsMisconfigured", "create_app", "redact_span_query"]

_log = get_logger("aira_gateway")


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Create and configure a Gateway FastAPI application."""
    settings = settings or GatewaySettings()
    # Before anything is built or a port opened: the convenience defaults are safe locally and are
    # the whole attack surface anywhere else (`security.py`).
    enforce_safe_settings(settings)
    otel_enabled = configure_process(settings)

    use_sqlite = settings.uses_sqlite()
    engine = build_engine(settings.database_url(use_sqlite=use_sqlite))
    sessionmaker = build_sessionmaker(engine)
    # Shared counters for rate limits and budget reservations (ADR-0008). An empty URL yields a
    # runner that reports itself unavailable, so both take their documented fallback.
    counters = build_runner(settings.redis_url, settings.redis_sentinel())

    # The API's own map — every route, the incident and content-read ones with their parameters —
    # is for whoever develops against it, not for anybody who can reach a deployment's port.
    documented = is_local_or_demo(settings)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=_lifespan(settings, engine, sessionmaker, counters, use_sqlite=use_sqlite),
        docs_url="/docs" if documented else None,
        redoc_url="/redoc" if documented else None,
        openapi_url="/openapi.json" if documented else None,
    )
    app.state.settings = settings
    _assemble_services(app, settings, engine, sessionmaker, counters)

    if otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, server_request_hook=redact_span_query)
        # And what this gateway calls (`FRD-117` FR-5): the model, and the database reads.
        instrument_outgoing_calls(engine)

    # Middleware runs **outermost-last**, and the order decides which responses carry the headers.
    # The body limit is innermost: it wraps `receive`, which nothing above it calls, so nothing is
    # buffered earlier — and every layer below adds its headers to its 413 as well.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    # Outside the ceiling, so it refuses on the path every route sees: `/uc/<slug>/kira/…` is a KIRA
    # request when it is too large, too, and is audited as one.
    app.add_middleware(UseCasePathMiddleware)
    # Outside both, so a browser can read a refusal from either rather than a network error.
    configure_cors(app, settings)
    app.add_middleware(SecurityHeadersMiddleware)
    # Outermost of ours, so a response produced by an exception handler still carries it (`FRD-117`
    # FR-4). Starlette answers an unhandled error outside all of them (`_handle_unexpected`).
    app.add_middleware(TraceIdMiddleware)
    # Outermost of all ours: the id is drawn before anything can refuse, so every answer has one.
    app.add_middleware(RequestIdMiddleware)

    _include_routers(app)
    register_exception_handlers(app)
    return app


def _build_providers(settings: GatewaySettings) -> ProviderRegistry:
    """Every configured upstream adapter, in one registry.

    One registry, so a self-hosted upstream passes the same gate, pipeline, dispatch chain and
    audit writer as a cloud one. Configuration an adapter cannot honour fails here, at startup.
    """
    providers: list[Upstream] = []
    # Deterministic fiction: right for the demo and the hermetic suite, never registered elsewhere —
    # a fake model serving real traffic, billed as free, cannot be "approved" (`FRD-307`).
    if is_local_or_demo(settings):
        providers.append(MockProvider())
    gemini = build_gemini_upstream(settings)
    if gemini is not None:
        providers.append(gemini)
    # Vertex EU (FRD-115): unusable credentials or a region this deployment does not permit fail
    # here, on purpose.
    providers.extend(build_vertex_upstreams(settings))
    # OpenAI-dialect servers (FRD-123), one adapter per declared machine, each audited by name.
    providers.extend(build_openai_upstreams(settings))
    # Microsoft Foundry (FRD-120): the OpenAI dialect over a different transport (`ADR-0011`).
    providers.extend(build_foundry_upstreams(settings))
    return ProviderRegistry(providers)


def _assemble_services(
    app: FastAPI,
    settings: GatewaySettings,
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    counters: ScriptRunner,
) -> None:
    """Put every service the request path uses on `app.state` (typed accessors: `state.py`)."""
    # One record of what the counter-backed features actually experience, so a gateway running on
    # its fallbacks says so instead of only answering a health-check ping.
    degradation = DegradationLog()
    registry = _build_providers(settings)
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    # Probed in the background and *read* by `/readyz` (`FRD-117` §5.2): probing inline would make
    # readiness as slow as the slowest upstream and wake a scaled-to-zero endpoint on every check.
    app.state.upstream_probe = UpstreamProbe(registry, degradation)
    app.state.pipeline_store = PipelineStore(sessionmaker)
    app.state.counters = counters
    app.state.degradation = degradation
    app.state.budgets = BudgetService(
        sessionmaker,
        enforce=settings.enforce_budgets,
        ledger=BudgetLedger(counters),
        degradation=degradation,
    )
    app.state.rate_limits = RateLimitService(
        sessionmaker,
        FallbackTokenBucket(RedisTokenBucket(counters), InMemoryTokenBucket(), degradation),
        enforce=settings.enforce_rate_limits,
    )
    app.state.pricing = PricingService(sessionmaker)
    app.state.catalog = ModelCatalog(sessionmaker)
    app.state.reporting = ReportingService(sessionmaker)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker
    app.state.oidc_validator = build_oidc_validator(settings)
    # `FRD-406`: credential shapes only. A redactor that mangles a prompt's business content makes
    # the stored payload useless, and the deployment then switches storage off — which is worse.
    app.state.redactor = build_redactor(settings.redact_patterns)
    # Read on every request and written a few times a week, so a cache over Postgres rather than
    # shared counters (`FRD-503` §4.1) — which also survives a Redis outage.
    app.state.suspensions = SuspensionService(sessionmaker, enforce=settings.enforce_suspensions)
    # Which use cases a token's Keycloak groups were granted (`FRD-209`); the same cache shape.
    app.state.group_grants = GroupGrantResolver(sessionmaker)
    # What each role may do (`FRD-614`); the same cache shape, refusing rather than admitting.
    app.state.role_definitions = RoleResolver(
        sessionmaker, settings.parsed_role_groups(), strict_data=settings.strict_data_mode
    )
    # Detection reads the rows the writer produces (`ADR-0014`), so every instance sees all the
    # traffic rather than its own share (`FRD-127`).
    app.state.anomalies = AnomalyService(
        sessionmaker,
        interval_seconds=settings.anomaly_interval_seconds,
        enabled=settings.detect_anomalies,
        suspensions=app.state.suspensions,
    )
    # The audit write happens after the response goes out (FRD-405 §4.4): persistence stays off
    # the request path.
    app.state.log_writer = RequestLogWriter(
        sessionmaker,
        settings,
        app.state.redactor,
        max_queue=settings.log_queue_size,
    )


def _lifespan(
    settings: GatewaySettings,
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    counters: ScriptRunner,
    *,
    use_sqlite: bool,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Start the background work before serving; drain and close everything on the way out."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # SQLite only: Alembic owns the schema of any real database, and an old container's
        # `create_all` once resurrected a table a migration had dropped (`FRD-114`).
        if use_sqlite:
            await create_all(engine)
        if settings.demo_mode:
            async with sessionmaker() as session:
                await ApiKeyService(session, demo_mode=True).ensure_demo_key()
        elif not is_local_environment(settings):
            await _revoke_the_published_demo_key(sessionmaker)
        await app.state.log_writer.start()
        # One probe *before* serving, so the first readiness answer is a verdict, not an absence.
        probe = app.state.upstream_probe
        await probe.probe_once()
        probe.start()
        await app.state.anomalies.start()
        yield
        await app.state.anomalies.stop()
        await probe.stop()
        # Drained, not dropped: a redeploy must not discard audit rows still in the queue.
        await app.state.log_writer.stop()
        await counters.close()
        # The upstream connection pools: one `httpx.AsyncClient` per configured provider.
        await app.state.providers.aclose()
        await engine.dispose()

    return lifespan


async def _revoke_the_published_demo_key(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Revoke a demo key a demo run left in a database that now serves a deployment.

    Verification refuses it already outside demo mode (`ApiKeyService.verify`). The row is revoked
    as well, terminally like any revoked key, because its plaintext is published and this database
    now serves without the declaration that made that acceptable. Locally a demo is switched on
    and off against one database, so the row is kept there. Never fails the start: the refusal at
    verification is the protection, and an unreachable database is `/readyz`'s to report.
    """
    try:
        async with sessionmaker() as session:
            revoked = await ApiKeyService(session).revoke(DEMO_KEY_PREFIX)
    except Exception as exc:  # noqa: BLE001 - see above: never a reason not to start
        _log.warning("demo_key_not_checked", error_type=type(exc).__name__)
        return
    if revoked:
        _log.warning(
            "demo_key_revoked",
            detail=(
                "This database held the published demo key from a run with AIRA_DEMO_MODE on. "
                "It has been revoked: outside demo mode it is a credential anybody can read."
            ),
        )


def _include_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(pipeline_router)
    app.include_router(usage_router)
    app.include_router(reporting_router)
    app.include_router(incidents_router)
    # What a vendor offers this installation (`FRD-507` stage C). Bounded by role and about the
    # installation, so not mounted under the Gemini surface's use-case attribution.
    app.include_router(providers_router)
    # KIRA resolves its own attribution (FRD-107 §5.3): one membership, or a header.
    app.include_router(kira_router)
    app.include_router(kira_info_router)
    app.include_router(gemini_router, dependencies=[Depends(require_attribution)])
    # Google's ranking path (`FRD-147`): attributed and authenticated like every model call.
    app.include_router(ranking_router, dependencies=[Depends(require_attribution)])


def redact_span_query(span: Any, scope: MutableMapping[str, Any]) -> None:
    """OTel server-request hook: keep credentials out of exported spans (`ADR-0007`).

    Gemini clients may authenticate with ``?key=<api key>``, which the ASGI instrumentation records
    verbatim. The query and the target are rewritten from the scope; then **every** string
    attribute is scrubbed (`telemetry.redact_span_urls`), because the instrumentation also records
    the whole URL, under a name of its own choosing.
    """
    if span is None or not span.is_recording():
        return
    query = bytes(scope.get("query_string", b"")).decode("latin-1")
    if not query:
        return
    redacted = redact_query_string(query)
    span.set_attribute("url.query", redacted)
    span.set_attribute("http.target", f"{scope.get('path', '')}?{redacted}")
    redact_span_urls(span)
