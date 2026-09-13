"""Observability wiring for the management backend, as functions so each is unit-testable."""

from __future__ import annotations

from typing import Any

from aira_common.integration_debug import report
from aira_common.observability import configure_observability
from aira_management import __version__
from aira_management.config.app_settings import ManagementSettings


def setup_observability(settings: ManagementSettings) -> bool:
    """Configure the OTel providers. Returns whether it ran.

    **Only the providers.** Instrumenting Django is :func:`instrument_django`, which has to run
    from `AppConfig.ready()` rather than from here.
    """
    return configure_observability(
        service_name=settings.app_name,
        service_version=__version__,
        environment=settings.environment,
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )


def instrument_django() -> bool:
    """Put OpenTelemetry's Django middleware in place. Returns whether it was added.

    **Must run after the settings module has finished executing**: `DjangoInstrumentor` inserts a
    middleware into `settings.MIDDLEWARE`, and called from `settings.py` it would insert into a
    list the later `MIDDLEWARE = [...]` assignment replaces — no span, and nothing saying so. Hence
    `AppConfig.ready()`: settings complete, apps loaded, no request served yet.

    Idempotent — `ready()` can run more than once under some runners, and instrumenting twice would
    put two middlewares on every request.
    """
    from django.conf import settings as django_settings
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    instrumentor = DjangoInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        return False
    instrumentor.instrument()
    return any(
        "opentelemetry" in middleware for middleware in getattr(django_settings, "MIDDLEWARE", [])
    )


def watch_database_connections() -> bool:
    """Say when Django opens a physical connection, and to where (`FRD-617` §3.3).

    The control plane's half of what `aira_gateway.db.base.watch_connections` does for the data
    plane. Django has no equivalent of SQLAlchemy's `handle_error`, so only the successful open is
    reported here — a failure to reach the database surfaces as an `OperationalError` through the
    ordinary error path, which the console already renders and logs.

    The address is assembled from the connection's own settings and **never** includes `PASSWORD`.

    Returns whether the receiver was connected, so `ready()` can say so and a test can assert it
    without reaching into Django's signal registry.
    """
    from django.db.backends.signals import connection_created
    from django.dispatch import receiver

    @receiver(connection_created, dispatch_uid="aira.integration_debug.postgres")
    def _opened(sender: object, connection: Any, **_kwargs: Any) -> None:
        params = connection.settings_dict
        host = params.get("HOST") or "localhost"
        port = params.get("PORT") or ""
        report(
            "postgres",
            "connect",
            target=f"{host}:{port}" if port else host,
            database=params.get("NAME"),
            user=params.get("USER"),
            vendor=getattr(connection, "vendor", None),
        )

    return True
