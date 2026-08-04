"""Django settings for the AIRA Management backend.

Values are derived from :class:`ManagementSettings` (env-driven, 12-factor). This is a
skeleton for Phase 0; RBAC apps, Keycloak OIDC, and domain models arrive in Phase 2.
"""

from __future__ import annotations

from pathlib import Path

from aira_common.logging import configure_logging
from aira_management.config.runtime import get_settings

_settings = get_settings()
configure_logging(_settings.log_level, json_output=_settings.log_json)

# Typed settings are also reachable via aira_management.config.runtime.get_settings().
AIRA = _settings

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = _settings.secret_key
DEBUG = _settings.debug
ALLOWED_HOSTS = _settings.allowed_hosts_list
APPEND_SLASH = False

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "aira_management.apps.health",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "aira_management.config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "aira_management.config.wsgi.application"
ASGI_APPLICATION = "aira_management.config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _settings.postgres_db,
        "USER": _settings.postgres_user,
        "PASSWORD": _settings.postgres_password,
        "HOST": _settings.postgres_host,
        "PORT": str(_settings.postgres_port),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}
