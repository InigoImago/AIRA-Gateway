"""ASGI entry point for the management backend."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aira_management.config.settings")

application = get_asgi_application()
