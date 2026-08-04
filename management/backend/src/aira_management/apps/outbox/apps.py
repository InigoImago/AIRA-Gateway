"""App config for the outbox app."""

from __future__ import annotations

from django.apps import AppConfig


class OutboxConfig(AppConfig):
    name = "aira_management.apps.outbox"
    label = "outbox"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from aira_management.apps.outbox.subscriber import record_to_outbox
        from aira_management.apps.usecases import events

        events.subscribe(record_to_outbox)
