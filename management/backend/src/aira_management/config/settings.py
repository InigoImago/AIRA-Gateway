"""Django settings for the AIRA Management backend.

Values are derived from :class:`ManagementSettings` (env-driven, 12-factor).
"""

from __future__ import annotations

import sys
from pathlib import Path

from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging
from aira_common.observability import set_payload_rendering
from aira_management.config.database import build_databases
from aira_management.config.observability import setup_observability
from aira_management.config.runtime import get_settings
from aira_management.config.security import effective_debug, enforce_safe_settings, is_local

_settings = get_settings()
configure_logging(_settings.log_level, json_output=_settings.log_json)
# One line per call to a system that is not ours, while one is being integrated (`FRD-617`).
configure_integration_debug(_settings.debug_integrations)
set_payload_rendering(_settings.debug_otel_payload)
# The **providers** only. Django is instrumented from `apps.api.ApiConfig.ready()`, because
# that instrumentation inserts a middleware and `MIDDLEWARE` is assigned below.
setup_observability(_settings)

# Refuse to boot a non-local deployment that still carries the development defaults (ADR-0007).
enforce_safe_settings(_settings)

# Typed settings are also reachable via aira_management.config.runtime.get_settings().
AIRA = _settings

BASE_DIR = Path(__file__).resolve().parent.parent

# Directory search (`FRD-209`) — read-only, and absent unless an admin client is configured.
AIRA_OIDC_ISSUER_BASE = _settings.oidc_issuer_base
AIRA_OIDC_REALM = _settings.oidc_realm
AIRA_ROLE_GROUPS = _settings.role_groups
AIRA_DIRECTORY_CLIENT_ID = _settings.directory_client_id
AIRA_DIRECTORY_CLIENT_SECRET = _settings.directory_client_secret
AIRA_DIRECTORY_URL = _settings.directory_url
AIRA_KAFKA_STAGE = _settings.kafka_stage

SECRET_KEY = _settings.secret_key
DEBUG = effective_debug(_settings)
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
    "aira_management.apps.catalog",
    "aira_management.apps.apikeys",
    "aira_management.apps.pipelines",
    "aira_management.apps.budgets",
    "aira_management.apps.ratelimits",
    "aira_management.apps.anomalies",
    "aira_management.apps.smoketests",
    "aira_management.apps.directory",
    "aira_management.apps.roles",
    "aira_management.apps.outbox",
    "aira_management.apps.privacy",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]
# No anonymous object-level permissions: skip guardian's anonymous-user provisioning.
ANONYMOUS_USER_NAME = None

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Response hardening (ADR-0007). The API is bearer-authenticated JSON with no cookies, so the
# relevant controls are the ones that stop a browser from reinterpreting or embedding it.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Ask browsers to stay on HTTPS once they have seen it. Not applied locally (plain HTTP), and
# deliberately without SECURE_SSL_REDIRECT: TLS termination happens in front of this service,
# and a redirect here would loop unless the proxy header is also configured.
SECURE_HSTS_SECONDS = 0 if is_local(_settings) else 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not is_local(_settings)

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
    # JSON only, through the parser that bounds nesting depth (`apps.api.parsers`): a control
    # plane whose API is a console's has no reason to accept a form post.
    "DEFAULT_PARSER_CLASSES": ["aira_management.apps.api.parsers.BoundedJSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "aira_management.apps.api.authentication.KeycloakJWTAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "aira_management.apps.api.exceptions.exception_handler",
    # How fast one authenticated caller may ask. `user` only, **not `AnonRateThrottle`**: DRF checks
    # permissions before throttles and every view requires authentication, so an anonymous
    # throttle would never run. The unauthenticated half is bounded in the authentication class,
    # counting refusals only (`apps.api.attempts`, `ADR-0015`). A throttled request answers `429`
    # through the usual error envelope.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": _settings.throttle_user},
}
