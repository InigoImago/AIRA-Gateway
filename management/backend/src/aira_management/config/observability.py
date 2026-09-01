"""Observability wiring for the management backend.

Kept as a function (rather than inline in ``settings.py``) so it is unit-testable.
"""

from __future__ import annotations

from typing import Any

from aira_common.integration_debug import report
from aira_common.observability import configure_observability
from aira_management import __version__
from aira_management.config.app_settings import ManagementSettings


def setup_observability(settings: ManagementSettings) -> bool:
    """Configure the OTel providers. Returns whether it ran.

    **Only the providers.** Instrumenting Django is :func:`instrument_django`, and it runs from an
    `AppConfig.ready()` rather than from here — see there for what happened when the two were one
    call.
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

    **This has to run after the settings module has finished executing, and it did not.**
    `DjangoInstrumentor` instruments by *inserting a middleware* into `settings.MIDDLEWARE`, and
    the call sat in `settings.py` above the `MIDDLEWARE = [...]` assignment — inside the very
    import Django performs to build the settings object. So it read a `MIDDLEWARE` that did not
    exist yet, inserted into a throwaway list, and the assignment forty lines below replaced
    whatever it had done.

    Measured on a running stack with `AIRA_OTEL_ENABLED=true`: `settings.MIDDLEWARE` held our three
    entries and no OpenTelemetry one, Tempo had seen `aira-gateway` and **never `aira-management`**,
    and every outbox row carried an empty `traceparent` — because there was no span to capture. The
    control plane had been exporting nothing for as long as the flag had existed, and nothing said
    so: the providers *were* configured, so every check short of asking for a span passed.

    `AppConfig.ready()` is the documented place and the only one that is late enough: settings are
    complete, apps are loaded, and no request has been served.

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
