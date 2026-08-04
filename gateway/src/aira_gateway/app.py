"""FastAPI application factory for the Gateway API.

``create_app`` builds an isolated application instance (no import-time side effects) so
tests can construct apps with custom settings. Production entry point lives in ``main``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aira_common.errors import AiraError
from aira_common.logging import configure_logging
from aira_common.observability import configure_observability
from aira_gateway import __version__
from aira_gateway.config import GatewaySettings
from aira_gateway.routes.health import router as health_router


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

    app = FastAPI(title=settings.app_name, version=__version__)
    app.state.settings = settings

    if otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)

    app.include_router(health_router)
    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AiraError)
    async def _handle_aira_error(_request: Request, exc: AiraError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(exclude_none=True),
        )
