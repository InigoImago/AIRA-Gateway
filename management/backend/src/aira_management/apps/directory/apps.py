"""App config for the directory search (FRD-209)."""

from __future__ import annotations

from django.apps import AppConfig


class DirectoryConfig(AppConfig):
    name = "aira_management.apps.directory"
    label = "directory"
    default_auto_field = "django.db.models.BigAutoField"
