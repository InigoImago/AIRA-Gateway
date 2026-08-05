"""FastAPI application factory for the Gateway API.

``create_app`` builds an isolated application instance (no import-time side effects) so
tests can construct apps with custom settings. Production entry point lives in ``main``.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from aira_common.counters import DegradationLog, build_runner
from aira_common.errors import AiraError, ErrorDetail, ErrorResponse
from aira_common.logging import configure_logging, get_logger
from aira_common.observability import (
    configure_observability,
    redact_query_string,
    trace_context_fields,
)
from aira_gateway import __version__
from aira_gateway.api.gemini.errors import GeminiHTTPError, gemini_error_response
from aira_gateway.api.gemini.routes import router as gemini_router
from aira_gateway.api.pipeline import router as pipeline_router
from aira_gateway.api.usage import router as usage_router
from aira_gateway.auth.dependencies import require_attribution
from aira_gateway.auth.oidc import build_oidc_validator
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.middleware import (
    BodySizeLimitMiddleware,
    RequestTooLarge,
    UseCasePathMiddleware,
)
from aira_gateway.persistence.redaction import NoOpRedactor
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
from aira_gateway.routes.health import router as health_router
from aira_gateway.upstreams.base import ProviderRegistry, Upstream
from aira_gateway.upstreams.gemini import build_gemini_upstream
from aira_gateway.upstreams.mock import MockProvider


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Create and configure a Gateway FastAPI application."""
    settings = settings or GatewaySettings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    otel_enabled = configure_observability(
        service_name=settings.app_name,
        service_version=__version__,
        environment=settings.environment,
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )

    use_sqlite = settings.test_database or ("pytest" in sys.modules)
    engine = build_engine(settings.database_url(use_sqlite=use_sqlite))
    sessionmaker = build_sessionmaker(engine)

    # Shared counters for rate-limit buckets and budget reservations (ADR-0008). An empty URL
    # yields a runner that reports itself unavailable, so both features take their documented
    # fallback rather than needing a second code path for "not configured".
    counters = build_runner(settings.redis_url)
    # One record of what the counter-backed features are actually experiencing, so a gateway
    # running on its fallbacks says so instead of only answering a health-check ping.
    degradation = DegradationLog()

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        await create_all(engine)
        if settings.demo_mode:
            async with sessionmaker() as session:
                await ApiKeyService(session).ensure_demo_key()
        await app_.state.log_writer.start()
        yield
        # Drained, not dropped: a redeploy must not discard audit rows still in the queue.
        await app_.state.log_writer.stop()
        await counters.close()
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # Deterministic mock is always available; the real Gemini provider is added when configured.
    providers: list[Upstream] = [MockProvider()]
    gemini = build_gemini_upstream(settings)
    if gemini is not None:
        providers.append(gemini)
    registry = ProviderRegistry(providers)
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
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
    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker
    app.state.oidc_validator = build_oidc_validator(settings)
    app.state.redactor = NoOpRedactor()
    # The audit write happens after the response goes out (FRD-405 §4.4); CLAUDE.md requires
    # persistence to stay off the request path and this is what makes that true.
    app.state.log_writer = RequestLogWriter(
        sessionmaker, settings, app.state.redactor, max_queue=settings.log_queue_size
    )

    if otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, server_request_hook=redact_span_query)

    # Middleware runs outermost-last: the body limit is added last so it is the first thing an
    # inbound request meets, before any of it is buffered.
    app.add_middleware(UseCasePathMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    app.include_router(health_router)
    app.include_router(pipeline_router)
    app.include_router(usage_router)
    app.include_router(gemini_router, dependencies=[Depends(require_attribution)])
    _register_exception_handlers(app)
    return app


_log = get_logger("aira_gateway")


def redact_span_query(span: Any, scope: MutableMapping[str, Any]) -> None:
    """OTel server-request hook: keep credentials out of exported spans.

    Gemini clients may authenticate with ``?key=<api key>``; the ASGI instrumentation would
    otherwise record that verbatim as a span attribute and ship it to the trace backend.
    """
    if span is None or not span.is_recording():
        return
    query = bytes(scope.get("query_string", b"")).decode("latin-1")
    if not query:
        return
    redacted = redact_query_string(query)
    span.set_attribute("url.query", redacted)
    span.set_attribute("http.target", f"{scope.get('path', '')}?{redacted}")


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestTooLarge)
    async def _handle_too_large(_request: Request, exc: RequestTooLarge) -> JSONResponse:
        return gemini_error_response(413, "Request body too large.", "INVALID_ARGUMENT")

    @app.exception_handler(AiraError)
    async def _handle_aira_error(_request: Request, exc: AiraError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(exclude_none=True),
        )

    @app.exception_handler(GeminiHTTPError)
    async def _handle_gemini_error(_request: Request, exc: GeminiHTTPError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort: log full context server-side, return a safe, shaped error to the client."""
        attribution = getattr(request.state, "attribution", None)
        _log.error(
            "unhandled_error",
            path=request.url.path,
            method=request.method,
            error_type=type(exc).__name__,
            error=str(exc),
            subject=getattr(attribution, "subject", None),
            use_case=getattr(attribution, "use_case", None),
            **trace_context_fields(),
        )
        # API routes get a Gemini-shaped error; other routes get the AIRA envelope.
        if request.url.path.startswith("/v1beta"):
            return gemini_error_response(
                500, "Internal error while processing the request.", "INTERNAL"
            )
        envelope = ErrorResponse(
            error=ErrorDetail(code="internal_error", message="Internal server error.")
        )
        return JSONResponse(status_code=500, content=envelope.model_dump(exclude_none=True))
