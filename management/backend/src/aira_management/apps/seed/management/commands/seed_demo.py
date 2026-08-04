"""``seed_demo`` management command (FRD-002).

Runs all registered seed contributions idempotently. Refuses to run in a production
environment unless ``--force`` is given; ``--fresh`` resets demo data first.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from aira_management.apps.seed import contributions as _contributions  # noqa: F401
from aira_management.apps.seed.registry import contributions as registered_contributions
from aira_management.config.runtime import get_settings


class Command(BaseCommand):
    help = "Seed demo data (idempotent). Use --fresh to reset demo data first."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--fresh", action="store_true", help="Reset demo data first.")
        parser.add_argument(
            "--force", action="store_true", help="Allow running in a production environment."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        settings = get_settings()
        if settings.environment == "production" and not options["force"]:
            raise CommandError("Refusing to seed in a production environment without --force.")

        fresh: bool = options["fresh"]
        steps = registered_contributions()
        for contribution in steps:
            summary = contribution.run(fresh)
            self.stdout.write(f"[seed] {contribution.name}: {summary}")

        self.stdout.write(self.style.SUCCESS(f"[seed] done ({len(steps)} contributions)"))
