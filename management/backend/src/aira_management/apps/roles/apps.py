"""App config for stored roles (`FRD-614`)."""

from __future__ import annotations

from django.apps import AppConfig


class RolesConfig(AppConfig):
    name = "aira_management.apps.roles"
    label = "roles"
    default_auto_field = "django.db.models.BigAutoField"
