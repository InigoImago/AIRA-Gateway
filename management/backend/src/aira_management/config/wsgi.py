"""WSGI entry point for the management backend."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aira_management.config.settings")

application = get_wsgi_application()
