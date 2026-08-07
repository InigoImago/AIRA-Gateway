"""App config for the anomaly-rules app (FRD-500)."""

from __future__ import annotations

from django.apps import AppConfig


class AnomaliesConfig(AppConfig):
    name = "aira_management.apps.anomalies"
    label = "anomalies"
    default_auto_field = "django.db.models.BigAutoField"
