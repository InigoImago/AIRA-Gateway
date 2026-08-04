"""App config for the seed app."""

from __future__ import annotations

from django.apps import AppConfig


class SeedConfig(AppConfig):
    name = "aira_management.apps.seed"
    label = "seed"

    def ready(self) -> None:
        # Import contributions so they register themselves in the seed registry.
        from aira_management.apps.seed import contributions  # noqa: F401
