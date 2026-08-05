"""App config for the DRF API app."""

from __future__ import annotations

from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "aira_management.apps.api"
    label = "api"
    default_auto_field = "django.db.models.BigAutoField"
