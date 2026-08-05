"""App config for the model catalog (FRD-403, the price half of FRD-307)."""

from __future__ import annotations

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "aira_management.apps.catalog"
    label = "catalog"
    default_auto_field = "django.db.models.BigAutoField"
