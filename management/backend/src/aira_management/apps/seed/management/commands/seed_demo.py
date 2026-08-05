"""``seed_demo`` management command (FRD-002).

Runs all registered seed contributions idempotently. ``--fresh`` resets demo data first.

Seeding creates well-known demo accounts (including a superuser) with a fixed password, so it
only runs where that is intended: locally, or with ``AIRA_DEMO_MODE`` explicitly on. Anywhere
else it refuses unless ``--force`` is given (ADR-0007).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from aira_management.apps.seed import contributions as _contributions  # noqa: F401
from aira_management.apps.seed.registry import contributions as registered_contributions
from aira_management.config.runtime import get_settings
from aira_management.config.security import is_local


class Command(BaseCommand):
    help = "Seed demo data (idempotent). Use --fresh to reset demo data first."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--fresh", action="store_true", help="Reset demo data first.")
        parser.add_argument(
            "--force", action="store_true", help="Allow running outside local/demo environments."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        settings = get_settings()
        if not (is_local(settings) or settings.demo_mode) and not options["force"]:
            raise CommandError(
                f"Refusing to seed demo accounts in environment '{settings.environment}' "
                "(set AIRA_DEMO_MODE, or pass --force if you really mean it)."
            )

        fresh: bool = options["fresh"]
        steps = registered_contributions()
        for contribution in steps:
            summary = contribution.run(fresh)
            self.stdout.write(f"[seed] {contribution.name}: {summary}")

        self.stdout.write(self.style.SUCCESS(f"[seed] done ({len(steps)} contributions)"))
