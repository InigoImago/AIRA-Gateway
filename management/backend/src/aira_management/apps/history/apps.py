"""App config for the configuration history."""

from __future__ import annotations

from django.apps import AppConfig


class HistoryConfig(AppConfig):
    name = "aira_management.apps.history"
    label = "history"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from aira_management.apps.history.recorder import record_change
        from aira_management.apps.usecases import events

        # After the outbox's subscriber, which `outbox` registers first: the outbox row of this
        # very change is then the newest of its key, and the one before it is what it replaced.
        events.subscribe(record_change)
