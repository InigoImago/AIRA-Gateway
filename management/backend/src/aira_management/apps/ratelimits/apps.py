"""App config for the rate-limits app (FRD-405)."""

from __future__ import annotations

from django.apps import AppConfig


class RateLimitsConfig(AppConfig):
    name = "aira_management.apps.ratelimits"
    label = "ratelimits"
    default_auto_field = "django.db.models.BigAutoField"
