"""Django settings for the AIRA Management backend.

Values are derived from :class:`ManagementSettings` (env-driven, 12-factor). This is a
skeleton for Phase 0; RBAC apps, Keycloak OIDC, and domain models arrive in Phase 2.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aira_common.logging import configure_logging
from aira_management.config.database import build_databases
from aira_management.config.observability import setup_observability
from aira_management.config.runtime import get_settings

_settings = get_settings()
configure_logging(_settings.log_level, json_output=_settings.log_json)
setup_observability(_settings)

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
    "guardian",
    "aira_management.apps.health",
    "aira_management.apps.seed",
    "aira_management.apps.api",
    "aira_management.apps.usecases",
    "aira_management.apps.apikeys",
    "aira_management.apps.outbox",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]
# No anonymous object-level permissions: skip guardian's anonymous-user provisioning.
ANONYMOUS_USER_NAME = None

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

# Use in-memory SQLite under pytest (or when explicitly requested) so tests are hermetic.
_use_sqlite = _settings.test_database or ("pytest" in sys.modules)
DATABASES = build_databases(_settings, _use_sqlite)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "aira_management.apps.api.authentication.KeycloakJWTAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "aira_management.apps.api.exceptions.exception_handler",
}
