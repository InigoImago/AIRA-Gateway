"""Observability wiring for the management backend.

Kept as a function (rather than inline in ``settings.py``) so it is unit-testable.
"""

from __future__ import annotations

from aira_common.observability import configure_observability
from aira_management import __version__
from aira_management.config.app_settings import ManagementSettings


def setup_observability(settings: ManagementSettings) -> bool:
    """Configure OTel export and instrument Django when enabled. Returns whether it ran."""
    enabled = configure_observability(
        service_name=settings.app_name,
        service_version=__version__,
        environment=settings.environment,
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )
    if enabled:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()
    return enabled
