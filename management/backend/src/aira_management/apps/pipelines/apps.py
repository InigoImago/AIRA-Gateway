"""App config for the pipelines app (FRD-300/303)."""

from __future__ import annotations

from django.apps import AppConfig


class PipelinesConfig(AppConfig):
    name = "aira_management.apps.pipelines"
    label = "pipelines"
    default_auto_field = "django.db.models.BigAutoField"
