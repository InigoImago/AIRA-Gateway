"""App config for the api-keys app (FRD-205)."""

from __future__ import annotations

from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    name = "aira_management.apps.apikeys"
    label = "apikeys"
    default_auto_field = "django.db.models.BigAutoField"
