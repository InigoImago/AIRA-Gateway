"""ASGI entry point for the management backend: Django, with the lifespan it does not speak."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aira_management.config.settings")

from aira_management.config.lifespan import with_lifespan  # noqa: E402 — after the settings
from aira_management.config.runtime import get_settings  # noqa: E402

application = with_lifespan(
    get_asgi_application(),  # type: ignore[arg-type]  # Django's handler is an ASGI callable
    service=get_settings().app_name,
)
