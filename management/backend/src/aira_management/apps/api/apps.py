"""App config for the DRF API app."""

from __future__ import annotations

from django.apps import AppConfig

from aira_management.config.observability import instrument_django, watch_database_connections
from aira_management.config.runtime import get_settings


class ApiConfig(AppConfig):
    name = "aira_management.apps.api"
    label = "api"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Instrument Django once the settings module has finished executing.

        `DjangoInstrumentor` inserts a middleware into `settings.MIDDLEWARE`, so it cannot run from
        `settings.py`, whose later assignment would replace its work (see
        `config.observability.instrument_django`).
        """
        if get_settings().otel_enabled:
            instrument_django()
        # Unconditional: the integration-debug channel decides per line, so the wiring itself is
        # never conditional on a setting (`FRD-617`).
        watch_database_connections()
