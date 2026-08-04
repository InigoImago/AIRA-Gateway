"""App config for the use-cases app."""

from __future__ import annotations

from django.apps import AppConfig


class UseCasesConfig(AppConfig):
    name = "aira_management.apps.usecases"
    label = "usecases"
    default_auto_field = "django.db.models.BigAutoField"
