"""App config for the privacy notice (`FRD-625`)."""

from __future__ import annotations

from django.apps import AppConfig


class PrivacyConfig(AppConfig):
    name = "aira_management.apps.privacy"
    label = "privacy"
    default_auto_field = "django.db.models.BigAutoField"
