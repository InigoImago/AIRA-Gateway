"""App config for the DRF API app."""

from __future__ import annotations

from django.apps import AppConfig

from aira_management.config.observability import instrument_django
from aira_management.config.runtime import get_settings


class ApiConfig(AppConfig):
    name = "aira_management.apps.api"
    label = "api"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Instrument Django, once the settings module has finished executing.

        `DjangoInstrumentor` works by inserting a middleware into `settings.MIDDLEWARE`, so it
        cannot run from `settings.py` — which is where it was, above the assignment that then
        replaced its work. This app is where it belongs: `ready()` is called after settings are
        complete and before any request is served. See `config.observability.instrument_django`
        for what the misplacement cost.
        """
        if get_settings().otel_enabled:
            instrument_django()
