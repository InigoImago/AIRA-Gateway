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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aira_common.counters import DegradationLog, build_runner
from aira_common.errors import AiraError, ErrorDetail, ErrorResponse
from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging, get_logger
from aira_common.observability import (
    configure_observability,
    redact_query_string,
    set_payload_rendering,
    trace_context_fields,
)
from aira_gateway import __version__
from aira_gateway.anomalies import AnomalyService
from aira_gateway.anomalies.suspensions import SuspensionService
from aira_gateway.api.gemini.errors import GeminiHTTPError, gemini_error_response
from aira_gateway.api.gemini.routes import router as gemini_router
from aira_gateway.api.incidents import router as incidents_router
from aira_gateway.api.kira.errors import KiraError, kira_error_response
from aira_gateway.api.kira.errors import code_for_status as kira_code_for_status
from aira_gateway.api.kira.errors import (
    code_for_unauthenticated as kira_code_for_unauthenticated,
)
from aira_gateway.api.kira.routes import BASE as KIRA_BASE
from aira_gateway.api.kira.routes import router as kira_router
from aira_gateway.api.pipeline import router as pipeline_router
from aira_gateway.api.providers import router as providers_router
from aira_gateway.api.reporting import router as reporting_router
from aira_gateway.api.usage import router as usage_router
from aira_gateway.auth.dependencies import require_attribution
from aira_gateway.auth.grants import GroupGrantResolver
from aira_gateway.auth.oidc import build_oidc_validator
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.budgets.ledger import BudgetLedger
from aira_gateway.budgets.service import BudgetService
from aira_gateway.catalog import ModelCatalog
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.diagnostics import UpstreamProbe
from aira_gateway.middleware import (
    BodySizeLimitMiddleware,
    RequestTooLarge,
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
from aira_gateway.security import enforce_safe_settings
from aira_gateway.telemetry import instrument_outgoing_calls
from aira_gateway.upstreams.base import ProviderRegistry, Upstream
from aira_gateway.upstreams.foundry import build_foundry_upstreams
from aira_gateway.upstreams.gemini import build_gemini_upstream
from aira_gateway.upstreams.mock import MockProvider
from aira_gateway.upstreams.openai import build_openai_upstreams
from aira_gateway.upstreams.vertex import build_vertex_upstreams


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Create and configure a Gateway FastAPI application."""
    settings = settings or GatewaySettings()
    # Before anything is built, and before a port is opened: the convenience defaults are safe
    # locally and are the whole attack surface anywhere else (`gateway/security.py`).
    enforce_safe_settings(settings)
    configure_logging(settings.log_level, json_output=settings.log_json)
    # Before the first exporter and the first token, because both are watched call sites
    # (`FRD-617`). Empty means off, which is what an installation that is not being
    # integrated runs.
    configure_integration_debug(settings.debug_integrations)
    set_payload_rendering(settings.debug_otel_payload)
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
        # Only where there are no migrations to contradict. `FRD-114` recorded the hazard from the
        # other end: an old container's `create_all` **resurrected a table a migration had
        # dropped**, and then failed every event against it. Alembic owns the schema of any real
        # database; `create_all` exists for SQLite, which has no migration history to disagree with.
        if use_sqlite:
            await create_all(engine)
        if settings.demo_mode:
            async with sessionmaker() as session:
                await ApiKeyService(session).ensure_demo_key()
        await app_.state.log_writer.start()
        # One probe *before* serving, so the first readiness answer is a verdict rather than an
        # absence — a fresh pod that reported "unknown" for its first minute would be indis-
        # tinguishable from one whose prober never started.
        probe = app_.state.upstream_probe
        await probe.probe_once()
        probe.start()
        await app_.state.anomalies.start()
        yield
        await app_.state.anomalies.stop()
        await probe.stop()
        # Drained, not dropped: a redeploy must not discard audit rows still in the queue.
        await app_.state.log_writer.stop()
        await counters.close()
        # The upstream connection pools. `create_app` opens one `httpx.AsyncClient` per configured
        # provider and this closed the engine, the counters, the writer and the probe — every
        # resource except those. Found by listing what is opened above against what is closed here.
        await app_.state.providers.aclose()
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # The mock answers with deterministic fiction. That is exactly what the demo and the hermetic
    # suite need and exactly what production must not have: a fake model serving real traffic,
    # billed as free, is worse than an ungoverned real one — and until 2026-08-09 it was registered
    # everywhere. `FRD-307` made the question unavoidable, because a test double cannot sensibly be
    # "approved by a Global Administrator".
    providers: list[Upstream] = []
    if settings.environment == "local" or settings.demo_mode:
        providers.append(MockProvider())
    gemini = build_gemini_upstream(settings)
    if gemini is not None:
        providers.append(gemini)
    # Vertex EU (FRD-115). Every failure it can raise — unusable credentials, a model in a region
    # this deployment does not permit — happens here, at startup, on purpose.
    providers.extend(build_vertex_upstreams(settings))
    # Servers speaking the OpenAI dialect (FRD-123) — one adapter per declared machine, each
    # audited under its own name. They go through the same registry as every other adapter, so
    # they pass the same pre-dispatch gate, pipeline, dispatch chain and audit writer: a
    # self-hosted upstream is governed exactly like a cloud one, or it is a hole beside it.
    providers.extend(build_openai_upstreams(settings))
    # Microsoft Foundry (FRD-120). The third platform, and the test of `ADR-0011`: it reuses the
    # OpenAI dialect unchanged and differs only in transport and addressing.
    providers.extend(build_foundry_upstreams(settings))
    registry = ProviderRegistry(providers)
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    # Reachability is probed in the background and *read* by `/readyz` (`FRD-117` §5.2). Probing
    # inline would make the readiness probe as slow as the slowest upstream and, against a
    # self-deployed model, would wake a scaled-to-zero endpoint on every check.
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
    # `FRD-406`. Narrow on purpose: credential shapes only, because a redactor that mangles the
    # business content of a prompt produces a stored payload nobody can use — and the deployment
    # then switches storage off, which is worse than storing it.
    app.state.redactor = build_redactor(settings.redact_patterns)
    # The audit write happens after the response goes out (FRD-405 §4.4); CLAUDE.md requires
    # persistence to stay off the request path and this is what makes that true.
    # Detection reads the rows the writer produces (`ADR-0014`): the writer tells it which scopes
    # saw traffic, and the timer does the rest. Constructed before the writer so the callback can
    # be handed over rather than patched in afterwards.
    # A suspension is read on every request and written a handful of times a week, so it is a
    # cache over Postgres rather than shared counter state — `FRD-503` §4.1 amends `ADR-0014` §2
    # and says why. It also survives a Redis outage, which for a control that *stops* traffic is
    # the direction that matters.
    app.state.suspensions = SuspensionService(sessionmaker, enforce=settings.enforce_suspensions)
    # Which use cases a token's Keycloak groups have been granted (`FRD-209`). Read on every
    # authenticated OIDC request, written rarely — a cache over the read-model, the same shape as
    # the suspension cache above.
    app.state.group_grants = GroupGrantResolver(sessionmaker)
    app.state.anomalies = AnomalyService(
        sessionmaker,
        interval_seconds=settings.anomaly_interval_seconds,
        enabled=settings.detect_anomalies,
        suspensions=app.state.suspensions,
    )
    # No `on_written` hook any more. It fed the anomaly evaluator's touched set, which is now read
    # from `request_logs` instead — the audit writer's own output — so the hook was telling the
    # evaluator something it can see for itself, and telling it only about *this* instance's share
    # of the traffic (`FRD-127`).
    app.state.log_writer = RequestLogWriter(
        sessionmaker,
        settings,
        app.state.redactor,
        max_queue=settings.log_queue_size,
    )

    if otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, server_request_hook=redact_span_query)
        # And what this gateway **calls** (`FRD-117` FR-5): the model, and the database read that
        # decides whether it is allowed. Specified in that FRD's §5.3, recorded there as delivered
        # from 2026-08-06, and never written — so a trace of a request that spent nine seconds
        # inside a model was one span, nine seconds long, with nothing underneath it. See
        # `telemetry.py` for the credential that had to not leak on the way in.
        instrument_outgoing_calls(engine)

    # Middleware runs **outermost-last**, and the order below is the whole of what that buys.
    #
    # It used to end with the body limit, on the reasoning that the ceiling must be the first
    # thing an inbound request meets. That reasoning is sound and the conclusion was wrong: the
    # ceiling is enforced by wrapping `receive`, and the two middlewares above it never call it,
    # so nothing was buffered any earlier or later either way. What the order *did* decide was
    # which responses carry the headers — and both of the ones that answer without reaching a
    # route came out bare. Measured: a 413 and a 500 with no `nosniff`, no `no-store` and no
    # trace id, which is precisely the pair `TraceIdMiddleware` names in its own docstring as the
    # reason it exists.
    _configure_cors(app, settings)
    app.add_middleware(UseCasePathMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    # Outermost, so a response produced by an exception handler still carries it (`FRD-117` FR-4).
    # The requests that most need correlating are the ones that went wrong.
    app.add_middleware(TraceIdMiddleware)

    app.include_router(health_router)
    app.include_router(pipeline_router)
    app.include_router(usage_router)
    app.include_router(reporting_router)
    app.include_router(incidents_router)
    # What a vendor offers this installation's credentials (`FRD-507` stage C). Mounted beside the
    # incident routes rather than inside the Gemini surface on purpose: it is bounded by **role**
    # and describes the installation, so attaching it to a surface that requires use-case
    # attribution would demand a use case in order to ask a question that has nothing to do with
    # one.
    app.include_router(providers_router)
    # The KIRA surface resolves its own attribution (FRD-107 §5.3): the predecessor has no
    # use-case selector, so the rule is "one membership, or a header" rather than a dependency.
    app.include_router(kira_router)
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


class CorsMisconfigured(Exception):
    """A CORS policy that would disable the protection it looks like it is providing."""


def _configure_cors(app: FastAPI, settings: GatewaySettings) -> None:
    """An explicit allow-list, defaulting to none (`FRD-117` §5.4).

    ``allow_origins=["*"]`` **with** ``allow_credentials=True`` is refused **at startup**.
    Browsers reject that combination outright, and a server that implements it by *reflecting* the
    origin disables the protection entirely: any site a user visits can then call this API with
    their credentials. Refused at startup rather than at request time — a misconfiguration that
    only shows up under a browser is one that ships.

    Empty by default because the SPA is served from the same origin through the proxy, so a
    deployment that needs cross-origin access is making a deliberate choice.
    """
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if not origins:
        return
    if "*" in origins and settings.cors_allow_credentials:
        raise CorsMisconfigured(
            "AIRA_CORS_ORIGINS='*' together with credentials would let any site call this API "
            "with a user's credentials. Name the origins instead."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-goog-api-key", "x-aira-use-case"],
        expose_headers=["x-trace-id", "retry-after", "deprecation", "sunset", "warning"],
    )


def _register_exception_handlers(app: FastAPI) -> None:
    def _kira(request: Request) -> bool:
        """Whether this request was aimed at the compatibility surface.

        The same test the routing and validation handlers already make. It was missing from the
        two handlers below, so a KIRA client meeting an oversized body or an invalid credential
        got Google's envelope — on the surface whose entire purpose is that a client migrates by
        changing a URL. `401` in particular is among the most commonly handled statuses there is,
        and `NOT_AUTHENTICATED` was already in the predecessor's vocabulary with nothing emitting
        it. Found by sending a KIRA request with no credential (2026-08-12).
        """
        return request.url.path.startswith(KIRA_BASE)

    @app.exception_handler(RequestTooLarge)
    async def _handle_too_large(request: Request, exc: RequestTooLarge) -> JSONResponse:
        message = "Request body too large."
        if _kira(request):
            return kira_error_response(413, kira_code_for_status(413), message)
        return gemini_error_response(413, message, "INVALID_ARGUMENT")

    @app.exception_handler(AiraError)
    async def _handle_aira_error(_request: Request, exc: AiraError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(exclude_none=True),
        )

    @app.exception_handler(GeminiHTTPError)
    async def _handle_gemini_error(request: Request, exc: GeminiHTTPError) -> JSONResponse:
        """A refusal that reached the application, rather than the route that could name it.

        The KIRA routes catch their own refusals (`KIRA_REFUSALS`) and render them in their own
        envelope, so what arrives here from that surface is what a **dependency** raised before
        the route ran — authentication, in practice, which uses `GeminiHTTPError` because that is
        the shared refusal type. It answered in Google's shape.
        """
        if _kira(request):
            code = kira_code_for_status(exc.code)
            if exc.code == 401:
                # The predecessor separates "nothing was presented" from "what was presented was
                # rejected", and the dependency records which one this was. See
                # `code_for_unauthenticated`: they are a configuration slip and a security signal,
                # and one bucket for both is a bucket nobody can act on.
                code = kira_code_for_unauthenticated(
                    bool(getattr(request.state, "credential_presented", False))
                )
            return kira_error_response(exc.code, code, exc.message)
        return exc.to_response()

    @app.exception_handler(KiraError)
    async def _handle_kira_error(_request: Request, exc: KiraError) -> JSONResponse:
        """A refusal this surface named itself, arriving from a route that did not catch it.

        Three of the routes wrap their body in ``except KIRA_REFUSALS`` and render the envelope
        there. `/ki-usage` did not, and there was no handler here, so **every** refusal it raises
        — a missing query parameter, a backwards time range, and worst of all the oversight-role
        check — left as ``500 internal_error`` in *Google's* envelope. Measured on 2026-08-12:
        four expectable errors, four 500s. A permission refusal reported as our own fault is the
        most misleading answer a governance system can give at that point, because the reader
        concludes the gateway is broken rather than that they lack a role.

        This is the third instance of one shape. The 174-case round found that this surface had no
        branch for a shared control's refusal, so every one became a 500; the envelope round found
        the two classes of refusal that never reach a route at all (no credential, oversized body).
        The third class is *a route that raises without catching* — and the answer is the same each
        time: **stop relying on every route to remember**. A handler here cannot be forgotten by a
        route that has not been written yet, and `test_kira_envelope_everywhere` now walks every
        route on the surface rather than the two that were known to be exposed.

        The routes keep their own ``except``: they need it anyway to close out the accounting, and
        a refusal that has already been rendered never reaches here.
        """
        return kira_error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_routing_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """A wrong URL or a wrong method, in the envelope of the surface it was aimed at.

        Without this, an unroutable path answered with the framework's own ``{"detail": "Not
        Found"}`` — a different shape from every other error the same API produces, for the caller
        least equipped to deal with one, since by definition they have not found the right route
        yet. Found by an edge case that asked what a percent-encoded path traversal returns: the
        status was right and the body was somebody else's.
        """
        detail = str(exc.detail) if exc.detail else "Not found."
        if request.url.path.startswith(KIRA_BASE):
            return kira_error_response(exc.status_code, "NOT_FOUND", detail)
        if request.url.path.startswith("/v1beta"):
            status = "NOT_FOUND" if exc.status_code == 404 else "INVALID_ARGUMENT"
            return gemini_error_response(exc.status_code, detail, status)
        envelope = ErrorResponse(error=ErrorDetail(code="not_found", message=detail))
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """A parameter the server will not accept, in the envelope of the surface it was sent to.

        The same finding as the routing handler above, one layer in: FastAPI answers `422` with its
        own ``{"detail": [...]}`` list, which is a different shape from every other error this API
        produces. A Google client reads `error.code` and `error.message`; handed a `detail` array it
        reports "unknown error" and the caller never learns that `limit` has a maximum of 200.

        **400, not 422.** The caller's job is to fix the request, and `INVALID_ARGUMENT` is the
        status the rest of this surface uses for exactly that. The message **names the parameter**,
        because "validation failed" is the answer that turns a two-minute fix into a support
        conversation.
        """
        first = exc.errors()[0] if exc.errors() else {}
        location = [str(part) for part in first.get("loc", ()) if part not in ("query", "body")]
        field = ".".join(location) or "request"
        message = f"{field}: {first.get('msg', 'is not acceptable')}."
        if request.url.path.startswith(KIRA_BASE):
            return kira_error_response(400, "INVALID_REQUEST", message)
        if request.url.path.startswith("/v1beta"):
            return gemini_error_response(400, message, "INVALID_ARGUMENT")
        envelope = ErrorResponse(error=ErrorDetail(code="invalid_request", message=message))
        return JSONResponse(status_code=400, content=envelope.model_dump())

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort: log full context server-side, return a safe, shaped error to the client.

        The **one** response `SecurityHeadersMiddleware` cannot reach, whatever the order above:
        Starlette answers an unhandled exception from `ServerErrorMiddleware`, which wraps the
        entire user middleware stack, so this response is written past every one of them. The
        headers are therefore applied here, from that middleware's own tuple rather than from a
        second list — a defence restated is a defence that drifts, and this is the response class
        least likely to be looked at again.

        **And it speaks the compatibility surface's envelope too**, which it did not. `_kira` is
        defined a few lines above and used by four handlers; this one branched on `/v1beta` alone,
        so an ordinary bug inside a KIRA route answered
        ``{"error":{"code":"internal_error",…}}`` — the AIRA envelope, on the surface whose entire
        contract is its error shape. Measured on 2026-08-15 by raising a `RuntimeError` inside
        `/kira/api/external/chat`.

        That is the **fourth** instance of the shape `_handle_kira_error` documents three times:
        a refusal class that never reaches the route that could name it. The routes' own `except`
        clauses cannot help here by construction — an unexpected exception is precisely the one no
        `except KIRA_REFUSALS` matches.
        """
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
        # Each surface in its own envelope; anything else gets the AIRA one.
        if _kira(request):
            response = kira_error_response(500, kira_code_for_status(500), "Internal server error.")
        elif request.url.path.startswith("/v1beta"):
            response = gemini_error_response(
                500, "Internal error while processing the request.", "INTERNAL"
            )
        else:
            envelope = ErrorResponse(
                error=ErrorDetail(code="internal_error", message="Internal server error.")
            )
            response = JSONResponse(status_code=500, content=envelope.model_dump(exclude_none=True))
        for name, value in SecurityHeadersMiddleware.HEADERS:
            response.headers.setdefault(name.decode("ascii"), value.decode("ascii"))
        return response
