"""App config for the budgets app (FRD-400)."""

from __future__ import annotations

from django.apps import AppConfig


class BudgetsConfig(AppConfig):
    name = "aira_management.apps.budgets"
    label = "budgets"
    default_auto_field = "django.db.models.BigAutoField"
